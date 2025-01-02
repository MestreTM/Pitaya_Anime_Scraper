from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import logging
import json
import re
import random
import time
import os
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
import urllib3
from playwright_stealth import stealth_sync

# ====================================================
#                  CONFIGURAÇÕES
# ====================================================

# Configurações do Flask
app = Flask(__name__)

# Configuração do Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///anime_embeds.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configuração do Cache
app.config['CACHE_TYPE'] = 'simple'

# Configuração do Rate Limiting
app.config['RATELIMIT_DEFAULT'] = "100 per hour"

# Configuração de Logging
logging.basicConfig(level=logging.INFO)

# Desabilita avisos de SSL inseguros
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# Lista de User Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/14.0.3 Safari/605.1.15",
]

# Lista de Proxies (opcional)
PROXIES = [
    # {"server": "http://proxy1:port", "username": "user1", "password": "pass1"},
    # {"server": "http://proxy2:port", "username": "user2", "password": "pass2"},
    # Adicione mais proxies conforme necessário
]

# API Key para autenticação
API_KEY = "123"

# ====================================================
#                INICIALIZAÇÕES
# ====================================================

# Inicialização do Banco de Dados
db = SQLAlchemy(app)

# Inicialização do Cache
cache = Cache(app)

# Inicialização do Rate Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=[app.config['RATELIMIT_DEFAULT']])
limiter.init_app(app)

# Definição do Modelo de Dados
class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False)
    response_data = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<EmbedRequest {self.url}>"

# Criação das tabelas no Banco de Dados
with app.app_context():
    db.create_all()
    logging.info("Banco de dados e tabelas criados ou já existentes.")

# Carregar configurações dos sites a partir do arquivo configs.json
with open('configs.json', 'r', encoding='utf-8') as config_file:
    site_configs = json.load(config_file)

# ====================================================
#              FUNÇÕES DE UTILIDADE
# ====================================================

def get_browser():
    """Inicializa Playwright, navegador e contexto com stealth."""
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    
    if PROXIES:
        proxy = random.choice(PROXIES)
        proxy_config = {
            "server": proxy["server"],
            "username": proxy.get("username"),
            "password": proxy.get("password")
        }
    else:
        proxy_config = None

    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": random.randint(1200, 1920), "height": random.randint(800, 1080)},
        locale="en-US",
        permissions=["geolocation"],
        timezone_id="America/Sao_Paulo",
        proxy=proxy_config
    )

    stealth_sync(context)
    return playwright, browser, context

def identify_site(url):
    """Identifica qual site está sendo requisitado com base na URL."""
    for site_key, config in site_configs.items():
        domain = config.get('domain')
        if domain in url:
            return site_key, config
    return None, None

def match_url_pattern(url, pattern):
    """Verifica se a URL corresponde ao padrão regex fornecido."""
    return re.match(pattern, url) is not None

def capture_screenshot(page, url, prefix="error"):
    """Captura uma screenshot da página atual e salva."""
    sanitized_url = re.sub(r'[^\w\-]', '_', url)
    screenshot_path = f'screenshots/{prefix}_{sanitized_url}.png'
    os.makedirs('screenshots', exist_ok=True)
    page.screenshot(path=screenshot_path)
    logging.info(f'Screenshot salva em {screenshot_path}')

def save_snapshot(episode_url, embed_url):
    """Salva uma snapshot do embed_url para o episode_url no cache."""
    key = f'snapshots:{episode_url}'
    cache.set(key, embed_url)
    logging.info(f'Snapshot salvo para {episode_url}: {embed_url}')

def get_last_embed_from_snapshots(episode_url):
    """Recupera o último embed_url salvo para o episode_url a partir do cache."""
    key = f'snapshots:{episode_url}'
    embed_url = cache.get(key)
    if embed_url:
        logging.info(f'Último embed_url recuperado da snapshot para {episode_url}: {embed_url}')
        return embed_url
    logging.warning(f'Nenhuma snapshot encontrada para {episode_url}.')
    return None

def scroll_page(page, scroll_pause_time=1.0):
    """Simula o scroll na página para carregar conteúdo dinâmico."""
    last_height = page.evaluate("() => document.body.scrollHeight")
    while True:
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(scroll_pause_time)
        new_height = page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def simulate_mouse_movements(page):
    """Simula movimentos de mouse para enganar scripts de detecção."""
    try:
        page.mouse.move(random.randint(0, 800), random.randint(0, 600))
        time.sleep(random.uniform(0.5, 1.5))
        page.mouse.move(random.randint(0, 800), random.randint(0, 600))
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logging.warning(f'Erro ao simular movimentos de mouse: {str(e)}')

def close_popups(page):
    """Fecha pop-ups ou modais que possam estar presentes na página."""
    try:
        popup_selectors = ['div.modal-close', 'button.close', 'button#close-popup']
        for selector in popup_selectors:
            element = page.query_selector(selector)
            if element:
                element.click()
                logging.info(f'Pop-up fechado com seletor: {selector}')
                time.sleep(random.uniform(0.5, 1.0))
    except Exception as e:
        logging.warning(f'Erro ao fechar pop-ups: {str(e)}')

def extract_embed_url(page, episode_url, selectors, retries=3):
    """Extrai o embed URL de um episódio individual com retries."""
    attempt = 0
    while attempt < retries:
        try:
            page.goto(episode_url, timeout=60000)
            scroll_page(page)  
            close_popups(page)  
            simulate_mouse_movements(page)  

            page_title = page.title()
            logging.info(f'Título da Página de Episódio: {page_title}')

            iframe_selectors = selectors.get('iframe_selectors', [])
            embed_url = None
            for selector in iframe_selectors:
                try:
                    iframe = page.wait_for_selector(selector, timeout=10000)
                    embed_url = iframe.get_attribute('src')
                    if embed_url:
                        logging.info(f'Embed URL encontrado com seletor "{selector}": {embed_url}')
                        break
                except PlaywrightTimeoutError:
                    logging.warning(f'Selecionador "{selector}" não encontrou um iframe válido dentro do timeout.')
                except Exception as e:
                    logging.warning(f'Erro ao procurar seletor "{selector}": {str(e)}')

            if embed_url:
                save_snapshot(episode_url, embed_url)
                return {'episode_url': episode_url, 'embed_url': embed_url}
            else:
                logging.error(f'Nenhum iframe válido encontrado para URL: {episode_url}')
                last_embed = get_last_embed_from_snapshots(episode_url)
                if last_embed:
                    logging.info(f'Recuperando embed URL da última snapshot para {episode_url}: {last_embed}')
                    return {'episode_url': episode_url, 'embed_url': last_embed, 'note': 'Recuperado da snapshot.'}
                else:
                    capture_screenshot(page, episode_url, prefix="no_iframe")
                    return {'episode_url': episode_url, 'error': 'Nenhum iframe válido encontrado e nenhuma snapshot disponível.'}
        
        except PlaywrightTimeoutError as e:
            attempt += 1
            logging.error(f'Erro ao carregar a página: {episode_url} na tentativa {attempt}: {str(e)}')
            if attempt < retries:
                wait_time = 2 ** attempt
                logging.info(f'Tentando novamente após {wait_time} segundos...')
                time.sleep(wait_time)
            else:
                capture_screenshot(page, episode_url, prefix="exception")
                return {'episode_url': episode_url, 'error': f'Erro ao extrair embed URL após {retries} tentativas: {str(e)}'}
        except Exception as e:
            attempt += 1
            logging.error(f'Erro inesperado ao extrair embed URL para {episode_url} na tentativa {attempt}: {str(e)}')
            if attempt < retries:
                wait_time = 2 ** attempt
                logging.info(f'Tentando novamente após {wait_time} segundos...')
                time.sleep(wait_time)
            else:
                capture_screenshot(page, episode_url, prefix="exception")
                return {'episode_url': episode_url, 'error': f'Erro inesperado ao extrair embed URL após {retries} tentativas: {str(e)}'}

def extract_episode_urls(page, anime_main_url, selectors):
    """Extrai todas as URLs de episódios da página principal do anime."""
    try:
        page.goto(anime_main_url, timeout=60000)
    except PlaywrightTimeoutError as e:
        logging.error(f'Erro ao carregar a página principal: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="timeout")
        return {'anime_main_url': anime_main_url, 'error': 'Erro ao carregar a página.'}
    except Exception as e:
        logging.error(f'Erro ao carregar a página principal: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="timeout")
        return {'anime_main_url': anime_main_url, 'error': 'Erro ao carregar a página.'}
    
    try:
        page_title = page.title()
        logging.info(f'Título da Página Principal: {page_title}')
        
        page.wait_for_selector(selectors['anime_main']['episodes_section'], timeout=30000)
    except PlaywrightTimeoutError as e:
        logging.error(f'Secção de episódios não encontrada para URL: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="no_episodes_section")
        return {'anime_main_url': anime_main_url, 'error': 'Seção de episódios não encontrada.'}
    except Exception as e:
        logging.error(f'Secção de episódios não encontrada para URL: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="no_episodes_section")
        return {'anime_main_url': anime_main_url, 'error': 'Seção de episódios não encontrada.'}
    
    try:
        episode_elements = page.query_selector_all(selectors['anime_main']['episodes_section'])
        episode_urls = [elem.get_attribute('href') for elem in episode_elements if elem.get_attribute('href')]
        logging.info(f'Encontradas {len(episode_urls)} URLs de episódios.')
        
        for idx, ep_url in enumerate(episode_urls[:5], start=1):
            logging.info(f'Episódio {idx}: {ep_url}')
        
        return {'anime_main_url': anime_main_url, 'episode_urls': episode_urls}
    except Exception as e:
        logging.error(f'Erro ao extrair URLs de episódios: {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="exception")
        return {'anime_main_url': anime_main_url, 'error': f'Erro ao extrair URLs de episódios: {str(e)}'}

# ====================================================
#                  ROTAS DA API
# ====================================================

@app.route('/get-embed', methods=['GET'])
@limiter.limit("5 per minute")
@cache.cached(timeout=3600, query_string=True)
def get_embed():
    """Endpoint para obter o embed URL de um anime ou episódio específico."""
    api_key = request.headers.get('X-API-KEY')
    logging.info(f"API Key recebida: {api_key}")
    if not api_key or api_key != API_KEY:
        logging.warning('Requisição sem ou com API Key inválida.')
        return jsonify({'error': 'API Key inválida ou ausente.'}), 401

    input_url = request.args.get('url')
    force_refresh = request.args.get('force', 'false').lower() == 'true'

    if not input_url:
        logging.warning('Requisição sem URL fornecida.')
        return jsonify({'error': 'Parâmetro "url" é obrigatório.'}), 400

    site_key, config = identify_site(input_url)

    if not site_key:
        logging.warning(f'URL inválida ou fora do domínio permitido: {input_url}')
        return jsonify({'error': 'URL inválida ou fora do domínio permitido.'}), 400

    embed_request = EmbedRequest.query.filter_by(url=input_url).first()

    if embed_request and not force_refresh:
        logging.info(f'Dados encontrados no banco de dados para URL: {input_url}')
        return jsonify(json.loads(embed_request.response_data)), 200

    url_patterns = config.get('url_patterns', {})
    selectors = config.get('selectors', {})

    response_payload = {}

    if match_url_pattern(input_url, url_patterns.get('anime_main', '')):
        try:
            logging.info(f'Processando página principal do anime: {input_url}')
            playwright, browser, context = get_browser()
            page = context.new_page()
            episodes_info = extract_episode_urls(page, input_url, selectors)

            if 'error' in episodes_info:
                page.close()
                context.close()
                browser.close()
                playwright.stop()
                return jsonify(episodes_info), 504

            episode_urls = episodes_info.get('episode_urls', [])
            embed_results = []

            for ep_url in episode_urls:
                time.sleep(random.uniform(1, 3))  # Delay aleatório
                embed_info = extract_embed_url(page, ep_url, selectors['episode'])
                embed_results.append(embed_info)

            page.close()
            context.close()
            browser.close()
            playwright.stop()
            response_payload = {'anime_main_url': input_url, 'episodes': embed_results}

        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500

    elif match_url_pattern(input_url, url_patterns.get('episode', '')):
        try:
            logging.info(f'Processando URL de episódio individual: {input_url}')
            playwright, browser, context = get_browser()
            page = context.new_page()
            embed_info = extract_embed_url(page, input_url, selectors['episode'])
            page.close()
            context.close()
            browser.close()
            playwright.stop()
            response_payload = embed_info

        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500

    else:
        logging.warning(f'URL não corresponde a nenhum padrão definido: {input_url}')
        return jsonify({'error': 'URL não corresponde a nenhum padrão definido.'}), 400

    try:
        if embed_request:
            embed_request.response_data = json.dumps(response_payload)
            embed_request.timestamp = db.func.now()
            logging.info(f'Atualizando dados no banco de dados para URL: {input_url}')
        else:
            new_request = EmbedRequest(url=input_url, response_data=json.dumps(response_payload))
            db.session.add(new_request)
            logging.info(f'Adicionando novos dados ao banco de dados para URL: {input_url}')

        db.session.commit()
    except SQLAlchemyError as e:
        logging.error(f'Erro ao salvar dados no banco de dados: {str(e)}')
        db.session.rollback()
        return jsonify({'error': f'Erro ao salvar dados no banco de dados: {str(e)}'}), 500

    return jsonify(response_payload), 200

@app.route('/reload-config', methods=['POST'])
def reload_config():
    """Endpoint para recarregar as configurações dos sites."""
    global site_configs
    try:
        with open('configs.json', 'r', encoding='utf-8') as config_file:
            site_configs = json.load(config_file)
        logging.info('Configurações recarregadas com sucesso.')
        return jsonify({'message': 'Configurações recarregadas com sucesso.'}), 200
    except Exception as e:
        logging.error(f'Erro ao recarregar configurações: {str(e)}')
        return jsonify({'error': f'Erro ao recarregar configurações: {str(e)}'}), 500

# ====================================================
#                  EXECUÇÃO
# ====================================================

if __name__ == '__main__':
    # Verifica se o arquivo de configurações existe
    if not os.path.exists('configs.json'):
        logging.error('Arquivo configs.json não encontrado na pasta raiz.')
        exit(1)
    
    # Certifique-se de que o diretório de screenshots existe
    os.makedirs('screenshots', exist_ok=True)
    
    # Inicia o servidor Flask
    app.run(debug=True)
