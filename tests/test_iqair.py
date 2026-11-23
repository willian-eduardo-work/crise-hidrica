from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automacao'))
from inventario_iqair import transformar, SemLeitura, main

AGORA = datetime(2026, 11, 16, 13, tzinfo=timezone.utc)


def payload():
    return dict(city='Porto Velho', state='Rondonia', country='Brazil',
                location={'coordinates': [-63.90389, -8.76194]},
                current={'pollution': dict(ts='2026-11-16T13:00:00Z', aqius=58,
                                          aqicn=19, mainus='p2', maincn='p2'),
                         'weather': dict(ts='2026-11-16T13:00:00Z', tp=32, hu=50,
                                         pr=1011, ws=1.94, wd=341, heatIndex=34, ic='01d')})


class IQAirTest(unittest.TestCase):
    def test_indices_nativos_e_unidades_preservadas(self):
        r = transformar(payload(), 'Porto Velho', AGORA)
        self.assertEqual((r['aqi_usa'], r['aqi_cn']), (58, 19))
        self.assertEqual((r['data_hora'], r['umidade'], r['velocidade_vento']),
                         ('2026-11-16T09:00:00-04:00', 50, 1.94))
        self.assertIsNone(r['estacao_id'])
        self.assertIsNone(r['indice_uv'])

    def test_cidade_errada_nao_pode_ser_carregada(self):
        with self.assertRaises(ValueError):
            transformar(payload(), 'Cacoal', AGORA)

    def test_sem_leitura_nao_vira_zero(self):
        p = payload(); p['current']['pollution'] = None
        with self.assertRaises(SemLeitura):
            transformar(p, 'Porto Velho', AGORA)

    def test_dado_antigo_futuro_e_sem_fuso_rejeitados(self):
        for ts in ('2025-04-11T13:00:00Z', '2026-11-17T13:00:00Z', '2026-11-16T13:00:00'):
            p = payload(); p['current']['pollution']['ts'] = ts
            with self.subTest(ts=ts), self.assertRaises(ValueError):
                transformar(p, 'Porto Velho', AGORA)

    def test_meteorologia_antiga_nao_e_atribuida_ao_iqa_atual(self):
        p = payload(); p['current']['weather']['ts'] = '2026-11-15T13:00:00Z'
        r = transformar(p, 'Porto Velho', AGORA)
        self.assertEqual(r['aqi_usa'], 58)
        self.assertIsNone(r['temperatura'])
        self.assertIsNone(r['umidade'])

    def test_numeros_invalidos_nao_entram_no_banco(self):
        for campo, valor in [('aqius', float('nan')), ('aqicn', -1), ('aqius', True)]:
            p = payload(); p['current']['pollution'][campo] = valor
            with self.subTest(campo=campo, valor=valor), self.assertRaises(ValueError):
                transformar(p, 'Porto Velho', AGORA)

    def test_catchup_nao_consulta_api_nem_exige_chave(self):
        with patch.object(sys, 'argv', ['iqair', '--gravar', '--data-referencia', '2025-01-01']), \
                patch('inventario_iqair.consultar') as consultar:
            main()
            consultar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
