from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
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

# Inicializar o aplicativo Flask
app = Flask(__name__)

# Configuracao do Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///anime_embeds.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar SQLAlchemy
db = SQLAlchemy(app)

# Configuracao basica de logging
logging.basicConfig(level=logging.INFO)

# Desabilitar logs de warning do urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# Configuracao do Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Configuracao do Flask-Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["100 per hour"])
limiter.init_app(app)

# Definicao do Modelo de Dados
class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False)
    response_data = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<EmbedRequest {self.url}>"

# Criar todas as tabelas no banco de dados se elas nao existirem
with app.app_context():
    db.create_all()
    logging.info("Banco de dados e tabelas criados ou ja existentes.")

# Carregar configuracoes dos sites a partir do arquivo configs.json
with open('configs.json', 'r', encoding='utf-8') as config_file:
    site_configs = json.load(config_file)

# Lista de User-Agents para rotacao
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/14.0.3 Safari/605.1.15",
]

# Funções de utilidade

def get_browser():
    """ Inicializa e retorna uma instancia do navegador com Playwright. """
    browser = sync_playwright().start().chromium.launch(headless=True)
    return browser

def identify_site(url):
    """ Identifica qual site esta sendo requisitado com base na URL. """
    for site_key, config in site_configs.items():
        domain = config.get('domain')
        if domain in url:
            return site_key, config
    return None, None

def match_url_pattern(url, pattern):
    """ Verifica se a URL corresponde ao padrao regex fornecido. """
    return re.match(pattern, url) is not None

def capture_screenshot(page, url, prefix="error"):
    """ Captura uma screenshot da pagina atual e salva. """
    sanitized_url = re.sub(r'[^\w\-]', '_', url)
    screenshot_path = f'screenshots/{prefix}_{sanitized_url}.png'
    os.makedirs('screenshots', exist_ok=True)
    page.screenshot(path=screenshot_path)
    logging.info(f'Screenshot salva em {screenshot_path}')

def extract_embed_url(page, episode_url, selectors):
    """ Extrai o embed URL de um episodio individual. """
    try:
        page.goto(episode_url)
    except Exception as e:
        logging.error(f'Erro ao carregar a pagina: {episode_url} - {str(e)}')
        capture_screenshot(page, episode_url, prefix="timeout")
        return {'episode_url': episode_url, 'error': 'Erro ao carregar a pagina.'}
    
    try:
        page_title = page.title()
        logging.info(f'Titulo da Pagina de Episodio: {page_title}')
        
        iframe_selectors = selectors.get('iframe_selectors', [])
        embed_url = None
        for selector in iframe_selectors:
            try:
                iframe = page.wait_for_selector(selector, timeout=5000)
                embed_url = iframe.get_attribute('src')
                if embed_url:
                    logging.info(f'Embed URL encontrado com seletor "{selector}": {embed_url}')
                    break
            except Exception:
                logging.warning(f'Selecionador "{selector}" nao encontrou um iframe valido.')
        
        if embed_url:
            return {'episode_url': episode_url, 'embed_url': embed_url}
        else:
            logging.error(f'Nenhum iframe valido encontrado para URL: {episode_url}')
            capture_screenshot(page, episode_url, prefix="no_iframe")
            return {'episode_url': episode_url, 'error': 'Nenhum iframe valido encontrado.'}
    
    except Exception as e:
        logging.error(f'Erro ao extrair embed URL para {episode_url}: {str(e)}')
        capture_screenshot(page, episode_url, prefix="exception")
        return {'episode_url': episode_url, 'error': f'Erro ao extrair embed URL: {str(e)}'}

def extract_episode_urls(page, anime_main_url, selectors):
    """ Extrai todas as URLs de episodios da pagina principal do anime. """
    try:
        page.goto(anime_main_url)
    except Exception as e:
        logging.error(f'Erro ao carregar a pagina principal: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="timeout")
        return {'anime_main_url': anime_main_url, 'error': 'Erro ao carregar a pagina.'}
    
    try:
        page_title = page.title()
        logging.info(f'Titulo da Pagina Principal: {page_title}')
        
        page.wait_for_selector(selectors['anime_main']['episodes_section'], timeout=30000)
    except Exception as e:
        logging.error(f'Secao de episodios nao encontrada para URL: {anime_main_url} - {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="no_episodes_section")
        return {'anime_main_url': anime_main_url, 'error': 'Secao de episodios nao encontrada.'}
    
    try:
        episode_elements = page.query_selector_all(selectors['anime_main']['episodes_section'])
        episode_urls = [elem.get_attribute('href') for elem in episode_elements if elem.get_attribute('href')]
        logging.info(f'Encontradas {len(episode_urls)} URLs de episodios.')
        
        for idx, ep_url in enumerate(episode_urls[:5], start=1):
            logging.info(f'Episodio {idx}: {ep_url}')
        
        return {'anime_main_url': anime_main_url, 'episode_urls': episode_urls}
    except Exception as e:
        logging.error(f'Erro ao extrair URLs de episodios: {str(e)}')
        capture_screenshot(page, anime_main_url, prefix="exception")
        return {'anime_main_url': anime_main_url, 'error': f'Erro ao extrair URLs de episodios: {str(e)}'}

# Rotas da API

@app.route('/get-embed', methods=['GET'])
@limiter.limit("5 per minute")
@cache.cached(timeout=3600, query_string=True)
def get_embed():
    """ Endpoint para obter o embed URL de um anime ou episodio especifico. """
    api_key = request.headers.get('X-API-KEY')
    logging.info(f"API Key recebida: {api_key}")
    if not api_key or api_key != "123":
        logging.warning('Requisicao sem ou com API Key invalida.')
        return jsonify({'error': 'API Key invalida ou ausente.'}), 401

    input_url = request.args.get('url')
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    if not input_url:
        logging.warning('Requisicao sem URL fornecida.')
        return jsonify({'error': 'Parametro "url" e obrigatorio.'}), 400
    
    site_key, config = identify_site(input_url)
    
    if not site_key:
        logging.warning(f'URL invalida ou fora do dominio permitido: {input_url}')
        return jsonify({'error': 'URL invalida ou fora do dominio permitido.'}), 400
    
    embed_request = EmbedRequest.query.filter_by(url=input_url).first()
    
    if embed_request and not force_refresh:
        logging.info(f'Dados encontrados no banco de dados para URL: {input_url}')
        return jsonify(json.loads(embed_request.response_data)), 200
    
    url_patterns = config.get('url_patterns', {})
    selectors = config.get('selectors', {})
    
    response_payload = {}
    
    if match_url_pattern(input_url, url_patterns.get('anime_main', '')):
        try:
            logging.info(f'Processando pagina principal do anime: {input_url}')
            browser = get_browser()
            page = browser.new_page()
            episodes_info = extract_episode_urls(page, input_url, selectors)
            
            if 'error' in episodes_info:
                page.close()
                browser.close()
                return jsonify(episodes_info), 504
            
            episode_urls = episodes_info.get('episode_urls', [])
            embed_results = []
            
            for ep_url in episode_urls:
                time.sleep(random.uniform(1, 3))
                embed_info = extract_embed_url(page, ep_url, selectors['episode'])
                embed_results.append(embed_info)
            
            page.close()
            browser.close()
            response_payload = {'anime_main_url': input_url, 'episodes': embed_results}
        
        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500
    
    elif match_url_pattern(input_url, url_patterns.get('episode', '')):
        try:
            logging.info(f'Processando URL de episodio individual: {input_url}')
            browser = get_browser()
            page = browser.new_page()
            embed_info = extract_embed_url(page, input_url, selectors['episode'])
            page.close()
            browser.close()
            response_payload = embed_info
        
        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500
    
    else:
        logging.warning(f'URL nao corresponde a nenhum padrao definido: {input_url}')
        return jsonify({'error': 'URL nao corresponde a nenhum padrao definido.'}), 400
    
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
    """ Endpoint para recarregar as configuracoes dos sites. """
    global site_configs
    try:
        with open('configs.json', 'r', encoding='utf-8') as config_file:
            site_configs = json.load(config_file)
        logging.info('Configuracoes recarregadas com sucesso.')
        return jsonify({'message': 'Configuracoes recarregadas com sucesso.'}), 200
    except Exception as e:
        logging.error(f'Erro ao recarregar configuracoes: {str(e)}')
        return jsonify({'error': f'Erro ao recarregar configuracoes: {str(e)}'}), 500

if __name__ == '__main__':
    if not os.path.exists('configs.json'):
        logging.error('Arquivo configs.json nao encontrado na pasta raiz.')
        exit(1)
    
    app.run(debug=True)
