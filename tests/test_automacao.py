import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automacao'))
from pipeline_diario import validar_periodo, montar_periodos
from AutomacaoDadosANABaixar import WebScraper, TimeoutException
from automacao_media_diaria_nivel_2026 import calcular_media_diaria


class PeriodosTest(unittest.TestCase):
    def test_exportacao_ana_sem_registro_e_valida(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / '5-15430000-ARIQUEMES.xls'
            arquivo.write_text('<table><tr><td>Data/Hora</td><td>Nivel adotado (cm)</td></tr>'
                               '<tr><td colspan="2">Nao ha registro para esta estacao no periodo selecionado</td></tr></table>', encoding='ascii')
            self.assertTrue(calcular_media_diaria(str(arquivo)).empty)

    def test_html_de_erro_nao_e_dia_sem_dados(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / '5-15430000-ARIQUEMES.xls'
            arquivo.write_text('<table><tr><td>Erro interno no servidor</td></tr></table>')
            with self.assertRaises(ValueError):
                calcular_media_diaria(str(arquivo))

    def test_sem_dados_nao_e_erro(self):
        dia = datetime(2026, 11, 9)
        vazio = pd.DataFrame(columns=['DIA_DATE'])
        self.assertEqual(validar_periodo(vazio, dia, dia), {dia.date()})

    def test_lacuna_preserva_demais_dias(self):
        dia = datetime(2026, 11, 8)
        medias = pd.DataFrame({'DIA_DATE': [dia.date()]})
        self.assertEqual(validar_periodo(medias, dia, dia + timedelta(days=1)),
                         {(dia + timedelta(days=1)).date()})

    def test_filtro_incorreto_continua_erro(self):
        dia = datetime(2026, 11, 9)
        with self.assertRaises(ValueError):
            validar_periodo(pd.DataFrame({'DIA_DATE': [(dia-timedelta(days=1)).date()]}), dia, dia)

    def test_virada_de_ano(self):
        periodos = montar_periodos(datetime(2027, 1, 2), 3)
        self.assertEqual([p[0].date().isoformat() for p in periodos],
                         ['2027-01-01', '2026-12-31', '2026-12-30'])


class RecuperacaoTest(unittest.TestCase):
    def test_timeout_reabre_sessao_sem_repetir_download(self):
        scraper = WebScraper('ignorado')
        scraper.driver = MagicMock()
        with patch.object(scraper, 'filter_station', side_effect=[TimeoutException(), None]) as filtrar, \
             patch.object(scraper, 'filter_periodo') as periodo, \
             patch.object(scraper, 'close') as fechar, \
             patch.object(scraper, 'start'), patch.object(scraper, 'navigate_to_filter'), \
             patch.object(scraper, 'download_data') as download, \
             patch('builtins.open', side_effect=OSError()):
            scraper.preparar_consulta('15430000', '5', datetime(2026,11,9), datetime(2026,11,9))
            self.assertEqual(filtrar.call_count, 2)
            periodo.assert_called_once()
            fechar.assert_called_once()
            download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
