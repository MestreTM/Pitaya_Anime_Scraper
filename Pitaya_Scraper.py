from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By  # Importar By
from selenium.webdriver.support.ui import WebDriverWait  # Importar WebDriverWait
from selenium.webdriver.support import expected_conditions as EC  # Importar expected_conditions
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
import logging
import urllib3
import json
import re
import random
import time
import os

# Inicializar o aplicativo Flask
app = Flask(__name__)

# Configuração do Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///anime_embeds.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)

cache = Cache(app, config={'CACHE_TYPE': 'simple'})

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour"] 
)
limiter.init_app(app) 

class EmbedRequest(db.Model):
    __tablename__ = 'embed_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True, nullable=False)
    response_data = db.Column(db.Text, nullable=False)  
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<EmbedRequest {self.url}>"

with app.app_context():
    db.create_all()
    logging.info("Banco de dados e tabelas criados ou já existentes.")

# Carregar configurações dos sites a partir do arquivo configs.json
with open('configs.json', 'r', encoding='utf-8') as config_file:
    site_configs = json.load(config_file)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/14.0.3 Safari/605.1.15",
    # Adicione mais User-Agents conforme necessário
]

chrome_options = Options()
chrome_options.add_argument("--headless")  # Ativa o modo headless para execução oculta
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-webgl")  # Desabilita WebGL
chrome_options.add_argument("--disable-software-rasterizer")  # Desabilita rasterizador de software
chrome_options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")  # Rotação de User-Agent


def get_driver():
    """
    Inicializa e retorna uma instância do WebDriver do Chrome.
    """
    service = Service(ChromeDriverManager().install())  # Inicializa o Service com o ChromeDriver
    driver = webdriver.Chrome(service=service, options=chrome_options) 
    return driver

def identify_site(url):

    for site_key, config in site_configs.items():
        domain = config.get('domain')
        if domain in url:
            return site_key, config
    return None, None

def match_url_pattern(url, pattern):

    return re.match(pattern, url) is not None

def capture_screenshot(driver, url, prefix="error"):
    
   # Captura uma screenshot da página atual do driver.

    sanitized_url = re.sub(r'[^\w\-]', '_', url)
    screenshot_path = f'screenshots/{prefix}_{sanitized_url}.png'
    os.makedirs('screenshots', exist_ok=True)
    driver.save_screenshot(screenshot_path)
    logging.info(f'Screenshot salva em {screenshot_path}')

def extract_embed_url(driver, episode_url, selectors):

     # Extrai o embed URL de um episódio individual utilizando os seletores fornecidos.

    try:
        driver.get(episode_url)
    except TimeoutException:
        logging.error(f'Tempo de carregamento excedido para URL: {episode_url}')
        capture_screenshot(driver, episode_url, prefix="timeout")
        return {'episode_url': episode_url, 'error': 'Tempo de carregamento da página excedido.'}
    
    try:
        page_title = driver.title
        logging.info(f'Título da Página de Episódio: {page_title}')
        
        iframe_selectors = selectors.get('iframe_selectors', [])
        
        embed_url = None
        for selector in iframe_selectors:
            try:
                iframe = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                embed_url = iframe.get_attribute('src')
                if embed_url:
                    logging.info(f'Embed URL encontrado com seletor "{selector}": {embed_url}')
                    break 
            except TimeoutException:
                logging.warning(f'Selecionador "{selector}" não encontrou um iframe válido.')
        
        if embed_url:
            return {'episode_url': episode_url, 'embed_url': embed_url}
        else:
            logging.error(f'Nenhum iframe válido encontrado para URL: {episode_url}')
            capture_screenshot(driver, episode_url, prefix="no_iframe")
            return {'episode_url': episode_url, 'error': 'Nenhum iframe válido encontrado.'}
    
    except Exception as e:
        logging.error(f'Erro ao extrair embed URL para {episode_url}: {str(e)}')
        capture_screenshot(driver, episode_url, prefix="exception")
        return {'episode_url': episode_url, 'error': f'Erro ao extrair embed URL: {str(e)}'}

def extract_episode_urls(driver, anime_main_url, selectors):
    """
    Extrai todas as URLs de episódios da página principal do anime utilizando o seletor fornecido.
    """
    try:
        driver.get(anime_main_url)
    except TimeoutException:
        logging.error(f'Tempo de carregamento excedido para URL: {anime_main_url}')
        capture_screenshot(driver, anime_main_url, prefix="timeout")
        return {'anime_main_url': anime_main_url, 'error': 'Tempo de carregamento da página excedido.'}
    
    try:
        page_title = driver.title
        logging.info(f'Título da Página Principal: {page_title}')
        
        WebDriverWait(driver, 30).until(  
            EC.presence_of_element_located((By.CSS_SELECTOR, selectors['anime_main']['episodes_section']))
        )
    except TimeoutException:
        logging.error(f'Seção de episódios não encontrada dentro do tempo limite para URL: {anime_main_url}')
        capture_screenshot(driver, anime_main_url, prefix="no_episodes_section")
        return {'anime_main_url': anime_main_url, 'error': 'Seção de episódios não encontrada dentro do tempo limite.'}
    
    try:
        episode_elements = driver.find_elements(By.CSS_SELECTOR, selectors['anime_main']['episodes_section'])
        episode_urls = [elem.get_attribute('href') for elem in episode_elements if elem.get_attribute('href')]
        logging.info(f'Encontradas {len(episode_urls)} URLs de episódios.')
        
        for idx, ep_url in enumerate(episode_urls[:5], start=1):
            logging.info(f'Episódio {idx}: {ep_url}')
        
        return {'anime_main_url': anime_main_url, 'episode_urls': episode_urls}
    except Exception as e:
        logging.error(f'Erro ao extrair URLs de episódios: {str(e)}')
        capture_screenshot(driver, anime_main_url, prefix="exception")
        return {'anime_main_url': anime_main_url, 'error': f'Erro ao extrair URLs de episódios: {str(e)}'}

def verify_api_key(key):
    """
    EIDITAR API KEY
    """
    # Defina a API Key que desejar aqui.
    API_KEY = "123"  # esta chave será utilizada nos comandos, por proteção decidi implementá-la.
    return key == API_KEY

# Definição das Rotas da API

@app.route('/get-embed', methods=['GET'])
@limiter.limit("5 per minute")  
@cache.cached(timeout=3600, query_string=True) 
def get_embed():
    api_key = request.headers.get('X-API-KEY')
    if not api_key or not verify_api_key(api_key):
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
    
    # Determina se a URL é da página principal do anime ou de um episódio
    url_patterns = config.get('url_patterns', {})
    selectors = config.get('selectors', {})
    
    response_payload = {}
    
    if match_url_pattern(input_url, url_patterns.get('anime_main', '')):
        # Caso a URL seja da página principal do anime
        try:
            logging.info(f'Processando página principal do anime: {input_url}')
            driver = get_driver()
            episodes_info = extract_episode_urls(driver, input_url, selectors)
            
            if 'error' in episodes_info:
                driver.quit()
                return jsonify(episodes_info), 504
            
            episode_urls = episodes_info.get('episode_urls', [])
            embed_results = []
            
            for ep_url in episode_urls:
                # Introduzir delays aleatórios para simular comportamento humano e evitar bloqueios :3
                time.sleep(random.uniform(1, 3))
                embed_info = extract_embed_url(driver, ep_url, selectors['episode'])
                embed_results.append(embed_info)
            
            driver.quit()
            response_payload = {'anime_main_url': input_url, 'episodes': embed_results}
        
        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            if driver:
                driver.quit()
            capture_screenshot(driver, input_url, prefix="exception")
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500
    
    elif match_url_pattern(input_url, url_patterns.get('episode', '')):
        # Caso a URL seja de um episódio individual
        try:
            logging.info(f'Processando URL de episódio individual: {input_url}')
            driver = get_driver()
            embed_info = extract_embed_url(driver, input_url, selectors['episode'])
            driver.quit()
            response_payload = embed_info
        
        except Exception as e:
            logging.error(f'Erro interno: {str(e)}')
            if driver:
                driver.quit()
            capture_screenshot(driver, input_url, prefix="exception")
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500
    
    else:
        # URL não corresponde a nenhum padrão conhecido
        logging.warning(f'URL não corresponde a nenhum padrão definido: {input_url}')
        return jsonify({'error': 'URL não corresponde a nenhum padrão definido.'}), 400
    
    try:
        if embed_request:
            embed_request.response_data = json.dumps(response_payload)
            embed_request.timestamp = db.func.now()
            logging.info(f'Atualizando dados no banco de dados para URL: {input_url}')
        else:
            new_request = EmbedRequest(
                url=input_url,
                response_data=json.dumps(response_payload)
            )
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
    """
    Endpoint para recarregar as configurações dos sites a partir do arquivo configs.json.
    """
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
    # Verifica se o arquivo configs.json existe
    if not os.path.exists('configs.json'):
        logging.error('Arquivo configs.json não encontrado na pasta raiz.')
        exit(1)
    
    app.run(debug=True)
