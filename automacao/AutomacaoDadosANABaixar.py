from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from datetime import datetime
import os
import shutil

def date_normalized(date):
    return date.strftime('%d/%m/%Y')

def find_chrome_path():
    """Tenta encontrar o caminho do Chrome instalado"""
    possible_paths = [
        os.environ.get('CHROME_BIN', ''),
        shutil.which('google-chrome') or '',
        shutil.which('chromium') or '',
        shutil.which('chromium-browser') or '',
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None

def create_driver(directory_path):
    """Cria uma instancia do WebDriver com configuracoes basicas"""
    chrome_options = webdriver.ChromeOptions()
    chrome_options.page_load_strategy = 'eager'
    chrome_options.add_argument('--disable-dev-shm-usage')
    if os.environ.get('CHROME_NO_SANDBOX') == '1':
        chrome_options.add_argument('--no-sandbox')
    if os.environ.get("ANA_HEADLESS") == "1":
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
    
    prefs = {
        "download.default_directory": directory_path,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    chrome_path = find_chrome_path()
    if chrome_path:
        chrome_options.binary_location = chrome_path
        print(f"Usando Chrome em: {chrome_path}")
    
    try:
        driver_path = os.environ.get('CHROMEDRIVER_PATH') or ChromeDriverManager().install()
        service = ChromeService(driver_path)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(15)
        print("Driver criado com sucesso")
        return driver
    except Exception as e:
        print(f"Erro ao criar driver: {str(e)}")
        raise

def wait_and_click(driver, by, value, timeout=10):
    """Funcao auxiliar para esperar e clicar em elementos"""
    element = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, value))
    )
    element.click()
    return element

def wait_and_send_keys(driver, by, value, keys, timeout=10):
    """Funcao auxiliar para esperar e enviar texto para elementos"""
    element = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, value))
    )
    element.clear()
    element.send_keys(keys)
    return element

class WebScraper:
    def __init__(self, download_dir):
        self.driver = None
        self.download_dir = download_dir
        
    def start(self):
        """Inicia o navegador e abre o site"""
        self.driver = create_driver(self.download_dir)
        self.driver.get("https://www.snirh.gov.br/hidrotelemetria")

    def wait_page(self):
        WebDriverWait(self.driver, 60).until(lambda driver: driver.execute_script('''
            return document.readyState !== 'loading' &&
                !(window.Sys && Sys.WebForms &&
                  Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack());
        '''))

    def preparar_consulta(self, code, origem, inicio, fim):
        """Recupera uma sessao travada antes de iniciar qualquer download."""
        for tentativa in range(1, 4):
            try:
                self.filter_station(code, origem=origem)
                self.filter_periodo(inicio, fim)
                return
            except TimeoutException:
                logging.warning('ANA excedeu o tempo de espera na consulta de %s; tentativa %d/3',
                                code, tentativa)
                try:
                    self.driver.save_screenshot(os.path.join(self.download_dir, f'timeout-{code}-{tentativa}.png'))
                    with open(os.path.join(self.download_dir, f'timeout-{code}-{tentativa}.html'),
                              'w', encoding='utf-8') as arquivo:
                        arquivo.write(self.driver.page_source)
                except Exception:
                    logging.warning('Nao foi possivel salvar diagnostico do timeout')
                if tentativa == 3:
                    raise
                self.close()
                self.driver = None
                self.start()
                self.navigate_to_filter()

    def postback(self, acao):
        self.driver.execute_script('''
            window.__anaConcluido = false;
            if (window.Sys && Sys.WebForms) {
                var manager = Sys.WebForms.PageRequestManager.getInstance();
                if (window.__anaFimHandler) manager.remove_endRequest(window.__anaFimHandler);
                window.__anaFimHandler = function() {
                    window.__anaConcluido = true;
                };
                manager.add_endRequest(window.__anaFimHandler);
            } else {
                window.__anaConcluido = true;
            }
        ''')
        acao()
        WebDriverWait(self.driver, 60).until(lambda driver: driver.execute_script('''
            return document.readyState !== 'loading' &&
                (typeof window.__anaConcluido === 'undefined' || window.__anaConcluido === true);
        '''))
        self.wait_page()
        
    def navigate_to_filter(self):
        """Navega ate a pagina de filtro"""
        wait_and_click(self.driver, By.LINK_TEXT, "Acesso ao Mapa", timeout=30)
        menu = WebDriverWait(self.driver, 30).until(EC.presence_of_element_located(
            (By.XPATH, "//li[@class='has-popup static' and @aria-haspopup='Menu1:submenu:7']//a")))
        ActionChains(self.driver).move_to_element(menu).perform()
        wait_and_click(self.driver, By.XPATH,
                       "//div[@id='Menu1']//ul[@id='Menu1:submenu:7']//a[@href='serieHistorica.aspx']", timeout=30)
        self.wait_page()
        WebDriverWait(self.driver, 30).until(EC.presence_of_element_located(
            (By.ID, 'cphCorpo_ctl01_txtPesquisa')))
        
    def filter_station(self, code, origem='5'):
        """Filtra a estacao pelo codigo"""
        self.wait_page()
        # O portal atualiza a lista de forma assincrona. Nunca selecionar
        # a primeira opcao enquanto ela ainda pertence a estacao anterior.
        def selecionar_estacao(driver):
            try:
                dropdown = Select(driver.find_element(By.ID, "cphCorpo_ctl01_lstEstacoes"))
                for opcao in dropdown.options:
                    if opcao.text.strip().startswith(f'{origem} - {code} -'):
                        return opcao.get_attribute('value')
                return False
            except (StaleElementReferenceException, NoSuchElementException):
                return False

        for tentativa in range(3):
            wait_and_send_keys(self.driver, By.ID, "cphCorpo_ctl01_txtPesquisa", code)
            self.driver.find_element(By.ID, 'cphCorpo_ctl01_txtPesquisa').send_keys(Keys.ENTER)
            time.sleep(0.5)
            self.wait_page()
            try:
                valor = WebDriverWait(self.driver, 10).until(selecionar_estacao)
                break
            except TimeoutException:
                if tentativa == 2:
                    raise
                logging.warning('Pesquisa sem resultado para %s; repetindo', code)
        self.postback(lambda: Select(self.driver.find_element(
            By.ID, 'cphCorpo_ctl01_lstEstacoes')).select_by_value(valor))
        logging.info('Estacao selecionada: %s', code)
        
    def filter_date(self, target_date: datetime):
        """Filtra por data"""
        self.filter_periodo(target_date, target_date)

    def filter_periodo(self, inicio: datetime, fim: datetime):
        """Aplica um intervalo inclusivo, preservando as medias de cada dia."""
        if fim.date() < inicio.date():
            raise ValueError('Fim anterior ao inicio')
        for campo, valor in (
            ('txtPeriodoDe', date_normalized(inicio)),
            ('txtPeriodoDeHr', '00:00'),
            ('txtPeriodoA', date_normalized(fim)),
            ('txtPeriodoAHr', '23:59'),
        ):
            wait_and_send_keys(self.driver, By.ID, 'cphCorpo_ctl01_' + campo, valor)
        self.postback(lambda: wait_and_click(self.driver, By.ID, 'cphCorpo_ctl01_imbAplicarDrHr'))
        logging.info('Periodo aplicado: %s a %s', inicio.date(), fim.date())
        
    def download_data(self):
        """Faz o download dos dados"""
        wait_and_click(self.driver, By.ID, "cphCorpo_btExportar", timeout=30)
        time.sleep(3)
        
            
    def close(self):
        """Fecha o navegador"""
        if self.driver: 
            self.driver.quit()
