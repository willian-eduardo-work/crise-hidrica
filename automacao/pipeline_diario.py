"""Coleta ANA e carga PostgreSQL. Use --validar para conferir sem gravar no banco."""
import argparse
from datetime import datetime, timedelta
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]


def montar_periodos(referencia, dias, inicio=None, fim=None):
    if inicio is not None or fim is not None:
        if inicio is None or fim is None or fim.date() < inicio.date():
            raise ValueError('Informe inicio e fim, com fim igual ou posterior ao inicio')
        return [(inicio, fim)]
    if dias < 1:
        raise ValueError('--dias deve ser positivo')
    return [(referencia - timedelta(days=i), referencia - timedelta(days=i))
            for i in range(1, dias + 1)]


def esperar_download(pasta, anteriores, timeout=120):
    limite = time.monotonic() + timeout
    anterior = None
    while time.monotonic() < limite:
        novos = [p for p in pasta.glob('5-*.xls*') if p not in anteriores]
        if novos and not list(pasta.glob('*.crdownload')):
            atual = sorted((str(p), p.stat().st_size) for p in novos)
            if atual == anterior and all(tamanho > 0 for _, tamanho in atual):
                return
            anterior = atual
        time.sleep(2)
    raise TimeoutError('Download ANA incompleto; carga cancelada.')


def validar_periodo(medias, inicio, fim):
    """Lacunas sao normais; datas fora do pedido indicam filtro incorreto."""
    esperados = {inicio.date() + timedelta(days=i)
                 for i in range((fim.date() - inicio.date()).days + 1)}
    encontrados = set(medias.DIA_DATE)
    if encontrados - esperados:
        raise ValueError('ANA exportou datas fora do periodo solicitado')
    return esperados - encontrados


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument('--validar', action='store_true')
    modos.add_argument('--sem-coleta', action='store_true')
    modos.add_argument('--somente-coleta', action='store_true',
                       help='Coleta e valida sem conectar ou gravar no banco')
    parser.add_argument('--dias', type=int, default=3)
    parser.add_argument('--estacoes', nargs='+', help='Codigos ANA a coletar; padrao: quatro estacoes originais')
    parser.add_argument('--permitir-sem-dados', action='store_true',
                        help=argparse.SUPPRESS)  # Compatibilidade; lacunas sempre sao aceitas.
    parser.add_argument('--data-referencia', type=datetime.fromisoformat,
                        help='Data ISO YYYY-MM-DD; coleta os dias anteriores a ela')
    parser.add_argument('--inicio', type=datetime.fromisoformat, help='Inicio inclusivo da carga retroativa (YYYY-MM-DD)')
    parser.add_argument('--fim', type=datetime.fromisoformat, help='Fim inclusivo da carga retroativa (YYYY-MM-DD)')
    args = parser.parse_args()
    referencia = args.data_referencia or datetime.now()
    if args.dias < 1:
        parser.error('--dias deve ser positivo')
    if (args.inicio or args.fim) and (args.data_referencia or args.validar or args.sem_coleta):
        parser.error('--inicio/--fim nao combinam com --data-referencia, --validar ou --sem-coleta')
    try:
        periodos = montar_periodos(referencia, args.dias, args.inicio, args.fim)
    except ValueError as erro:
        parser.error(str(erro))
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(logs / 'pipeline.log', maxBytes=5_000_000,
                            backupCount=5, encoding='utf-8')],
        format='%(asctime)s %(levelname)s %(message)s', force=True)
    import automacao_media_diaria_nivel_2026 as carga
    codigos = args.estacoes or list(carga.ESTACOES)
    if len(set(codigos)) != len(codigos) or any(c not in carga.ESTACOES for c in codigos):
        parser.error('--estacoes deve conter codigos conhecidos e sem repeticao')
    if not (args.validar or args.somente_coleta) and not os.environ.get('DATABASE_URL'):
        raise ValueError('Configure DATABASE_URL no arquivo .env na raiz do projeto')
    if args.validar:
        arquivos = list(Path(carga.DIRETORIO_BASE).glob('5-*.xls*'))
        if not arquivos:
            raise FileNotFoundError('Nenhuma planilha ANA para validar')
        for arquivo in arquivos:
            if carga.extrair_codigo_estacao(arquivo.name) not in carga.ESTACOES:
                raise ValueError('Estacao desconhecida: ' + arquivo.name)
            medias = carga.calcular_media_diaria(str(arquivo))
            if medias.empty:
                logging.info('%s: fonte sem leituras validas; nenhuma media a gravar', arquivo.name)
            logging.info('%s: %d dias validos', arquivo.name, len(medias))
        return
    if args.sem_coleta:
        carga.main()
        return
    from AutomacaoDadosANABaixar import WebScraper
    # Cada execucao usa pasta nova: arquivos antigos nao mascaram falhas de coleta.
    pasta = ROOT / 'dados' / 'coletas' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    pasta.mkdir(parents=True)
    scraper = WebScraper(str(pasta))
    try:
        scraper.start()
        scraper.navigate_to_filter()
        for codigo in codigos:
            for inicio, fim in periodos:
                anteriores = set(pasta.iterdir())
                logging.info('Solicitando: %s de %s a %s', codigo, inicio.date(), fim.date())
                # Um postback travado nao deve obrigar a repetir as estacoes ja coletadas.
                scraper.preparar_consulta(codigo, carga.ESTACOES[codigo]['origem'], inicio, fim)
                scraper.download_data()
                esperar_download(pasta, anteriores)
                novos = [p for p in pasta.glob('5-*.xls*') if p not in anteriores]
                if len(novos) != 1 or carga.extrair_codigo_estacao(novos[0].name) != codigo:
                    raise ValueError('Download nao corresponde a estacao solicitada')
                medias = carga.calcular_media_diaria(str(novos[0]))
                ausentes = validar_periodo(medias, inicio, fim)
                if ausentes:
                    logging.info('Fonte ANA %s: %d dias sem leitura; lacunas preservadas', codigo, len(ausentes))
                logging.info('Coletado: %s de %s a %s (%d medias)', codigo, inicio.date(), fim.date(), len(medias))
    except Exception:
        if scraper.driver:
            try:
                scraper.driver.save_screenshot(str(pasta / 'falha.png'))
                (pasta / 'falha.html').write_text(scraper.driver.page_source, encoding='utf-8')
                logging.error('Diagnostico da pagina salvo em %s', pasta)
            except Exception:
                logging.error('Nao foi possivel salvar diagnostico da pagina')
        raise
    finally:
        scraper.close()
    arquivos = list(pasta.glob('5-*.xls*'))
    if len(arquivos) != len(codigos) * len(periodos):
        raise ValueError('Quantidade inesperada de planilhas; carga cancelada')
    for arquivo in arquivos:
        if carga.extrair_codigo_estacao(arquivo.name) not in carga.ESTACOES:
            raise ValueError('Estacao inesperada no download')
        medias = carga.calcular_media_diaria(str(arquivo))
    if args.somente_coleta:
        logging.info('Coleta validada sem carga no banco: %s', pasta)
        return
    if not any(not carga.calcular_media_diaria(str(a)).empty for a in arquivos):
        logging.warning('Consulta concluida; fonte sem dados para carga no periodo solicitado')
        return
    carga.processar_diretorio(str(pasta))
    logging.info('Coleta e carga concluidas')


if __name__ == '__main__':
    try:
        main()
    except Exception as erro:
        # Excecoes de drivers podem incluir strings de conexao; nao registrar valores.
        logging.error('Pipeline falhou (%s). Verifique configuracao, rede e formato ANA.',
                      type(erro).__name__)
        for frame in traceback.extract_tb(erro.__traceback__):
            if Path(frame.filename).parent == Path(__file__).parent:
                logging.error('Local: %s:%s em %s', Path(frame.filename).name, frame.lineno, frame.name)
        raise SystemExit(1)
