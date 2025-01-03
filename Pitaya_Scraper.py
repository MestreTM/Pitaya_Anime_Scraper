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

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///anime_embeds.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CACHE_TYPE'] = 'simple'
app.config['RATELIMIT_DEFAULT'] = "100 per hour"
logging.basicConfig(level=logging.INFO)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/14.0.3 Safari/605.1.15",
]

PROXIES = []
API_KEY = "123" #altere para a sua api desejada aqui.

db = SQLAlchemy(app)
cache = Cache(app)
limiter = Limiter(key_func=get_remote_address, default_limits=[app.config['RATELIMIT_DEFAULT']])
limiter.init_app(app)

class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False)
    response_data = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

with app.app_context():
    db.create_all()
    logging.info("Banco de dados e tabelas criados ou já existentes.")

with open('configs.json', 'r', encoding='utf-8') as config_file:
    site_configs = json.load(config_file)

# ====================================================
#                 FUNÇÕES PRINCIPAIS
# ====================================================

def get_browser():
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
    for site_key, cfg in site_configs.items():
        domain = cfg.get('domain')
        if domain in url:
            return site_key, cfg
    return None, None

def match_url_pattern(url, pattern):
    if not pattern:
        return False
    return re.match(pattern, url) is not None

def capture_screenshot(page, url, prefix="error"):
    try:
        filename = f"screenshots/{prefix}_{int(time.time())}.png"
        page.screenshot(path=filename)
        logging.info(f"Screenshot salva: {filename}")
    except Exception as e:
        logging.error(f"Falha ao capturar screenshot para {url}: {str(e)}")

def save_snapshot(episode_url, embed_url):
    logging.info(f"Snapshot salva: {episode_url} => {embed_url}")

def get_last_embed_from_snapshots(episode_url):
    return None

def bypass_inject_iframe_and_get_episode_links(page, main_url, episodes_section_selector):
    page.set_content(f"""
    <html>
      <body>
        <iframe src="{main_url}" sandbox></iframe>
      </body>
    </html>
    """, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    episode_urls = []
    for frame in page.frames:
        if frame.url == main_url:
            try:
                frame.wait_for_selector(episodes_section_selector, timeout=30000)
                elements = frame.query_selector_all(episodes_section_selector)
                for elem in elements:
                    href = elem.get_attribute('href')
                    if href:
                        episode_urls.append(href)
            except TimeoutError as e:
                logging.error(f"Seção de episódios não encontrada (iframe) em {main_url}: {str(e)}")
                capture_screenshot(page, main_url, prefix="no_episodes_section_iframe")
                return []
            except Exception as e:
                logging.error(f"Erro ao extrair episódios no iframe (main): {str(e)}")
                capture_screenshot(page, main_url, prefix="no_episodes_section_iframe")
                return []
    return episode_urls

def bypass_inject_iframe_and_get_player_srcs(page, episode_url):
    page.set_content(f"""
    <html>
      <body>
        <iframe src="{episode_url}" sandbox></iframe>
      </body>
    </html>
    """, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    player_srcs = []
    for frame in page.frames:
        if frame.url == episode_url:
            try:
                found_iframes = frame.evaluate("""
                    () => Array.from(document.querySelectorAll('iframe[src]'))
                              .map(i => i.getAttribute('src'))
                """)
                if found_iframes:
                    player_srcs.extend(found_iframes)
            except Exception as e:
                logging.warning(f"Não foi possível avaliar <iframe> no frame do episódio. Erro: {str(e)}")
    return list(set(player_srcs))

def extract_episode_urls(page, anime_main_url, config):
    bypass_js = config.get("bypass_javascript", False)
    selectors = config.get("selectors", {})
    anime_main = selectors.get('anime_main', {})
    episodes_section_selector = anime_main.get('episodes_section')

    if not episodes_section_selector:
        logging.error("Configuração 'anime_main.episodes_section' ausente ou inválida.")
        return {'anime_main_url': anime_main_url, 'error': "Configuração 'episodes_section' não encontrada."}

    try:
        if bypass_js:
            logging.info("bypass_javascript = true => Injetando iframe para PAGE PRINCIPAL")
            episode_urls = bypass_inject_iframe_and_get_episode_links(page, anime_main_url, episodes_section_selector)
            logging.info(f"Encontradas {len(episode_urls)} URLs de episódios (via bypass).")
            return {'anime_main_url': anime_main_url, 'episode_urls': episode_urls}
        else:
            page.goto(anime_main_url, timeout=60000)
            page_title = page.title()
            logging.info(f"Título da Página Principal: {page_title}")
            page.wait_for_selector(episodes_section_selector, timeout=30000)
            elements = page.query_selector_all(episodes_section_selector)
            episode_urls = [elem.get_attribute('href') for elem in elements if elem.get_attribute('href')]
            logging.info(f"Encontradas {len(episode_urls)} URLs de episódios (modo normal).")
            return {'anime_main_url': anime_main_url, 'episode_urls': episode_urls}
    except TimeoutError as e:
        logging.error(f"Timeout ao acessar a página principal {anime_main_url}: {str(e)}")
        capture_screenshot(page, anime_main_url, prefix="main_timeout")
        return {'anime_main_url': anime_main_url, 'error': f'Timeout: {str(e)}'}
    except Exception as e:
        logging.error(f"Erro ao extrair episodios do main {anime_main_url}: {str(e)}")
        capture_screenshot(page, anime_main_url, prefix="main_exception")
        return {'anime_main_url': anime_main_url, 'error': f'Erro geral: {str(e)}'}

def extract_embed_url(page, episode_url, config, retries=3):
    attempt = 0
    bypass_js = config.get("bypass_javascript", False)
    iframe_selectors = config.get("selectors", {}).get("episode", {}).get("iframe_selectors", [])

    while attempt < retries:
        try:
            if bypass_js:
                logging.info("bypass_javascript = true => Injetando IFRAME para EPISÓDIO")
                player_links = bypass_inject_iframe_and_get_player_srcs(page, episode_url)
                if player_links:
                    embed_url = player_links[0]
                    logging.info(f"Embed URL encontrado (bypass) em {episode_url}: {embed_url}")
                    save_snapshot(episode_url, embed_url)
                    return {'episode_url': episode_url, 'embed_url': embed_url}
                else:
                    logging.warning(f"Nenhum <iframe src='...'> encontrado no iframe bypass para {episode_url}.")
                    last_embed = get_last_embed_from_snapshots(episode_url)
                    if last_embed:
                        return {'episode_url': episode_url, 'embed_url': last_embed, 'note': 'Recuperado da snapshot.'}
                    capture_screenshot(page, episode_url, prefix="bypass_no_links")
                    return {'episode_url': episode_url, 'error': 'Nenhum player (iframe) encontrado no iframe injetado.'}
            else:
                page.goto(episode_url, timeout=60000)
                embed_url = None
                for selector in iframe_selectors:
                    try:
                        iframe_el = page.wait_for_selector(selector, timeout=5000)
                        frame_handle = iframe_el.content_frame()
                        if frame_handle:
                            current_src = iframe_el.get_attribute("src")
                            if current_src:
                                embed_url = current_src
                                logging.info(f"Embed URL encontrado com selector {selector}: {current_src}")
                                break
                    except TimeoutError:
                        logging.info(f"Iframe selector não encontrado (timeout) em: {selector}")
                    except Exception as e:
                        logging.warning(f"Erro ao inspecionar iframe com {selector}: {str(e)}")
                if embed_url:
                    save_snapshot(episode_url, embed_url)
                    return {'episode_url': episode_url, 'embed_url': embed_url}
                else:
                    logging.error(f"Nenhum iframe válido encontrado para URL: {episode_url}")
                    last_embed = get_last_embed_from_snapshots(episode_url)
                    if last_embed:
                        return {'episode_url': episode_url, 'embed_url': last_embed, 'note': 'Recuperado da snapshot.'}
                    capture_screenshot(page, episode_url, prefix="no_iframe")
                    return {'episode_url': episode_url, 'error': 'Nenhum iframe válido encontrado e nenhuma snapshot disponível.'}
        except TimeoutError as e:
            attempt += 1
            wait_time = 2 ** attempt
            logging.error(f"Timeout ao carregar {episode_url} (tentativa {attempt}): {str(e)}")
            if attempt < retries:
                logging.info(f"Aguardando {wait_time}s antes de nova tentativa...")
                time.sleep(wait_time)
            else:
                capture_screenshot(page, episode_url, prefix="exception_timeout")
                return {'episode_url': episode_url, 'error': f'Erro após {retries} tentativas: {str(e)}'}
        except Exception as e:
            attempt += 1
            wait_time = 2 ** attempt
            logging.error(f"Erro inesperado (tentativa {attempt}) ao extrair embed URL para {episode_url}: {str(e)}")
            if attempt < retries:
                logging.info(f"Aguardando {wait_time}s antes de nova tentativa...")
                time.sleep(wait_time)
            else:
                capture_screenshot(page, episode_url, prefix="exception_unexpected")
                return {'episode_url': episode_url, 'error': f'Erro inesperado após {retries} tentativas: {str(e)}'}

# ====================================================
#              ENDPOINT /get-embed
# ====================================================

@app.route('/get-embed', methods=['GET'])
@limiter.limit("5 per minute")
@cache.cached(timeout=3600, query_string=True)
def get_embed():
    api_key = request.headers.get('X-API-KEY')
    logging.info(f"API Key recebida: {api_key}")
    if not api_key or api_key != API_KEY:
        logging.warning('Requisição sem ou com API Key inválida.')
        return jsonify({'error': 'API Key inválida ou ausente.'}), 401

    input_url = request.args.get('url')
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    continue_flag = request.args.get('continue', 'false').lower() == 'true'

    if not input_url:
        logging.warning('Requisição sem URL fornecida.')
        return jsonify({'error': 'Parâmetro "url" é obrigatório.'}), 400

    site_key, config = identify_site(input_url)
    if not site_key:
        logging.warning(f'URL inválida ou fora do domínio permitido: {input_url}')
        return jsonify({'error': 'URL inválida ou fora do domínio permitido.'}), 400

    embed_request = EmbedRequest.query.filter_by(url=input_url).first()
    if embed_request and not force_refresh and not continue_flag:
        logging.info(f'Dados encontrados no banco de dados para URL: {input_url}')
        return jsonify(json.loads(embed_request.response_data)), 200

    old_data = {}
    processed_eps = []
    if embed_request and continue_flag:
        try:
            old_data = json.loads(embed_request.response_data)
            if 'episodes' in old_data:
                processed_eps = [
                    ep['episode_url'] for ep in old_data['episodes']
                    if 'episode_url' in ep and 'embed_url' in ep
                ]
        except Exception:
            pass

    url_patterns = config.get('url_patterns', {})
    playwright, browser, context = get_browser()
    page = context.new_page()
    response_payload = {}

    try:
        if match_url_pattern(input_url, url_patterns.get('anime_main', '')):
            logging.info(f'Processando página principal do anime: {input_url}')
            episodes_info = extract_episode_urls(page, input_url, config)
            if 'error' in episodes_info:
                page.close()
                context.close()
                browser.close()
                playwright.stop()
                return jsonify(episodes_info), 504

            episode_urls = episodes_info.get('episode_urls', [])
            embed_results = []
            if 'episodes' in old_data:
                embed_results = old_data['episodes']

            for ep_url in episode_urls:
                if ep_url in processed_eps:
                    logging.info(f"Pulando episódio já processado: {ep_url}")
                    continue

                time.sleep(random.uniform(1, 3))
                embed_info = extract_embed_url(page, ep_url, config)
                embed_results.append(embed_info)
                partial_payload = {
                    'anime_main_url': input_url,
                    'episodes': embed_results
                }
                if embed_request:
                    embed_request.response_data = json.dumps(partial_payload)
                    embed_request.timestamp = db.func.now()
                else:
                    embed_request = EmbedRequest(url=input_url, response_data=json.dumps(partial_payload))
                    db.session.add(embed_request)

                try:
                    db.session.commit()
                except SQLAlchemyError as e:
                    db.session.rollback()
                    logging.error(f'Erro ao salvar dados parciais: {str(e)}')

            response_payload = {
                'anime_main_url': input_url,
                'episodes': embed_results
            }

        elif match_url_pattern(input_url, url_patterns.get('episode', '')):
            logging.info(f'Processando URL de episódio individual: {input_url}')
            embed_info = extract_embed_url(page, input_url, config)
            response_payload = embed_info

        else:
            logging.warning(f'URL não corresponde a nenhum padrão definido: {input_url}')
            page.close()
            context.close()
            browser.close()
            playwright.stop()
            return jsonify({'error': 'URL não corresponde a nenhum padrão definido.'}), 400

    except Exception as e:
        if "'dict' object has no attribute '_object'" in str(e):
            # Aqui notificamos o usuário para que ele tente novamente com &continue=true
            logging.error("Erro do Playwright: 'dict' vs. objeto. Oriente o usuário a usar &continue=true.")
            page.close()
            context.close()
            browser.close()
            playwright.stop()
            return jsonify({
                'error': "Ocorreu um erro no Playwright. Tente novamente com &continue=true para retomar de onde parou."
            }), 500
        else:
            logging.error(f'Erro interno ao processar {input_url}: {str(e)}')
            page.close()
            context.close()
            browser.close()
            playwright.stop()
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500

    page.close()
    context.close()
    browser.close()
    playwright.stop()

    try:
        if isinstance(response_payload, dict) and 'episodes' in response_payload:
            final_data = json.dumps(response_payload)
            if embed_request:
                embed_request.response_data = final_data
                embed_request.timestamp = db.func.now()
                logging.info(f'Atualizando dados no banco de dados para URL: {input_url}')
            else:
                new_request = EmbedRequest(url=input_url, response_data=final_data)
                db.session.add(new_request)
                logging.info(f'Adicionando novos dados ao banco de dados para URL: {input_url}')
            db.session.commit()
        else:
            final_data = json.dumps(response_payload)
            if embed_request:
                embed_request.response_data = final_data
                embed_request.timestamp = db.func.now()
            else:
                embed_request = EmbedRequest(url=input_url, response_data=final_data)
                db.session.add(embed_request)
            db.session.commit()

    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f'Erro ao salvar dados no banco de dados: {str(e)}')
        return jsonify({'error': f'Erro ao salvar dados no banco de dados: {str(e)}'}), 500

    return jsonify(response_payload), 200

# ====================================================
#          ENDPOINT /reload-config
# ====================================================

@app.route('/reload-config', methods=['POST'])
def reload_config():
    global site_configs
    try:
        with open('configs.json', 'r', encoding='utf-8') as config_file:
            site_configs = json.load(config_file)
        logging.info('Configurações recarregadas com sucesso.')
        return jsonify({'message': 'Configurações recarregadas com sucesso.'}), 200
    except Exception as e:
        logging.error(f'Erro ao recarregar configurações: {str(e)}')
        return jsonify({'error': f'Erro ao recarregar configurações: {str(e)}'}), 500

if __name__ == '__main__':
    if not os.path.exists('configs.json'):
        logging.error('Arquivo configs.json não encontrado na pasta raiz.')
        exit(1)
    os.makedirs('screenshots', exist_ok=True)
    app.run(debug=True)
