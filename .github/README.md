
# Pitaya Scraper
![imagem](https://i.imgur.com/8Pi1WZK.png)

Pitaya Scraper é uma aplicação web em Python desenvolvida para extração de URLs de embeds de vídeos e informações relacionadas de páginas de animes ou de filmes. 

## Recursos

- **Scraping de animes**: Extrai URLs de episódios e embeds de páginas de animes.
- **Banco de Dados**: Armazena resultados em um banco de dados SQLite local.
- **APIs REST**: Fornece endpoints para acesso às funcionalidades.
- **Limites de Requisição**: Usa Flask-Limiter para evitar abuso.
- **Caching**: Implementado com Flask-Caching para otimizar o desempenho e eficiência.
- **User-Agent Rotativo**: Alterna entre diferentes agentes de usuário para evitar bloqueios.
- **Execução Headless**: Usa Playwright em modo headless para interações com páginas.

## Tecnologias Utilizadas

- **Flask**: Framework para construção de APIs web.
- **Playwright**: Automação de navegadores para scraping.
- **SQLAlchemy**: ORM para manipulação do banco de dados.
- **Flask-Caching**: Cache para respostas frequentes.
- **Flask-Limiter**: Controle de limite de requisições.
- **WebDriver Manager**: Gerenciamento automático do driver Chrome.

## Configuração

1. **Instale os requisitos**:

   ```bash
   pip install -r requirements.txt
   ```

2. **Crie um arquivo `configs.json`** com as configurações dos sites a serem scrapados. Exemplo:

   ```json
   {
       "site_key": {
           "domain": "example.com",
           "url_patterns": {
               "anime_main": "regex_para_pagina_principal",
               "episode": "regex_para_episodio"
           },
           "selectors": {
               "anime_main": {
                   "episodes_section": "css_selector_para_lista_episodios"
               },
               "episode": {
                   "iframe_selectors": ["css_selector_para_iframe"]
               }
           }
       }
   }
   ```
   - Você tambem pode baixar o arquivo `configs.json` com padrões teste já configurados (pode ficar desatualizado).

3. **Inicie o servidor**:

   ```bash
   python Pitaya_Scraper.py
   ```

## Endpoints

### `GET /get-embed`
Obtém o embed URL para um anime ou episódio.

**Parâmetros**:
- `url`: URL da página do anime ou episódio.
- `force`: (opcional) força atualização, mesmo que os dados estejam no cache.

**Cabeçalhos**:
- `X-API-KEY`: Chave de API para autenticação.

- Exemplo:
```bash
curl -H "X-API-KEY: 123" "http://127.0.0.1:5000/get-embed?url=https://exemplo.com/anime/principal"
```

### `POST /reload-config`
Recarrega as configurações do arquivo `configs.json`.
- Exemplo:
```bash
curl -X POST "http://127.0.0.1:5000/reload-config"
```

## Observações

- Certifique-se de que o arquivo `configs.json` está configurado corretamente.
- O arquivo `configs.json` deve estar na pasta raiz do projeto.
- Este projeto utiliza um banco de dados SQLite, mas pode ser adaptado para outros SGBDs.

## Licença

Este projeto é distribuído sob a licença MIT. Veja o arquivo LICENSE para mais detalhes.

---

*o projeto ainda está em desenvolvimento. Bugs podem acontecer...*
