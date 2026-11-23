"""Exporta cidades e leituras IQAir; --gravar tambem carrega no banco.

A listagem representa a cobertura atual da chave, nao um cadastro completo
de estacoes de RO nem um historico de estacoes sazonais.
"""
import csv
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


class SemLeitura(Exception):
    """Cidade da listagem ficou sem leitura; manter a lacuna."""


def consultar(endpoint, parametros, chave):
    url = 'https://api.airvisual.com/v2/' + endpoint + '?' + urlencode(
        dict(parametros, key=chave))
    for tentativa in range(3):
        try:
            with urlopen(url, timeout=30) as resposta:
                payload = json.load(resposta)
        except HTTPError as erro:
            if erro.code == 429 and tentativa < 2:
                time.sleep(60)
                continue
            try:
                falha = json.load(erro)
            except (ValueError, UnicodeError):
                falha = {}
            if falha.get('data', {}).get('message') in ('no_data', 'city_not_found'):
                raise SemLeitura('Cidade sem leitura publicada') from None
            # Nao incluir URL, chave nem corpo de erro no diagnostico.
            raise RuntimeError('IQAir HTTP ' + str(erro.code)) from None
        if payload.get('status') != 'success':
            raise RuntimeError('IQAir nao confirmou sucesso')
        return payload['data']
    raise RuntimeError('Limite IQAir atingido')


def transformar(dados, cidade, agora=None):
    if (dados.get('city') != cidade or dados.get('state') != 'Rondonia'
            or dados.get('country') != 'Brazil'):
        raise ValueError('Resposta nao corresponde a localidade pedida')
    atual = dados.get('current') or {}
    p, w = atual.get('pollution'), atual.get('weather') or {}
    if not p or not p.get('ts'):
        raise SemLeitura('Cidade sem leitura publicada')
    instante = datetime.fromisoformat(p['ts'].replace('Z', '+00:00'))
    if instante.tzinfo is None:
        raise ValueError('Horario de poluicao sem fuso')
    agora = agora or datetime.now(timezone.utc)
    idade = (agora - instante).total_seconds()
    if idade > 86400 or idade < -600:
        raise ValueError('Leitura IQAir antiga ou no futuro')
    for campo in ('aqius', 'aqicn'):
        if (isinstance(p[campo], bool) or not isinstance(p[campo], (int, float))
                or not math.isfinite(p[campo]) or p[campo] < 0 or int(p[campo]) != p[campo]):
            raise ValueError('Indice IQAir invalido')
    lon, lat = dados['location']['coordinates']
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Coordenadas invalidas')
    # Nao associar meteorologia antiga a um IQA recente. A resposta bruta
    # preserva os dois timestamps para auditoria.
    if w:
        tempo_clima = datetime.fromisoformat(w['ts'].replace('Z', '+00:00'))
        if tempo_clima.tzinfo is None:
            raise ValueError('Horario da meteorologia sem fuso')
        if abs((tempo_clima - instante).total_seconds()) > 10800:
            w = {}
    for campo in ('tp', 'pr', 'hu', 'ws', 'wd', 'heatIndex'):
        if w.get(campo) is not None:
            n = w[campo]
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
                raise ValueError('Meteorologia invalida')
    if w.get('hu') is not None and not 0 <= w['hu'] <= 100:
        raise ValueError('Umidade fora de 0-100')
    if w.get('ws') is not None and w['ws'] < 0:
        raise ValueError('Vento negativo')
    return dict(cidade=cidade, estado='Rondonia', pais='Brazil',
                tipo_localidade='cidade', estacao_id=None, estacao_nome=None,
                latitude=lat, longitude=lon,
                data_hora=instante.astimezone(timezone(timedelta(hours=-4))).isoformat(),
                data_hora_utc=instante.isoformat(), data_hora_clima_utc=w.get('ts'),
                aqi_usa=p['aqius'], aqi_cn=p['aqicn'],
                poluente_principal_usa=p.get('mainus'),
                poluente_principal_cn=p.get('maincn'),
                temperatura=w.get('tp'), pressao=w.get('pr'), umidade=w.get('hu'),
                velocidade_vento=w.get('ws'), direcao_vento=w.get('wd'),
                sensacao_termica=w.get('heatIndex'), icone_clima=w.get('ic'),
                indice_uv=None, fonte='IQAir / AirVisual - cidade')


def salvar(conn, linhas):
    from sqlalchemy import text
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('carga_iqair_cidades'))"))
    campos = ('aqi_usa', 'aqi_cn', 'poluente_principal_usa', 'poluente_principal_cn',
              'temperatura', 'pressao', 'umidade', 'velocidade_vento', 'direcao_vento',
              'sensacao_termica', 'icone_clima', 'indice_uv')
    for linha in linhas:
        conn.execute(text('''INSERT INTO public.localidade(cidade,estado,pais,latitude,longitude)
            VALUES (:cidade,:estado,:pais,:latitude,:longitude)
            ON CONFLICT (cidade) DO NOTHING'''), linha)
        localidade = conn.execute(text('''SELECT id FROM public.localidade
            WHERE cidade=:cidade AND estado=:estado AND pais=:pais'''), linha).scalar_one()
        registro = dict(linha, localidade_id=localidade,
                        data_hora=datetime.fromisoformat(linha['data_hora']).replace(tzinfo=None))
        ids = conn.execute(text('''SELECT id FROM public.historico_qualidade_ar
            WHERE localidade_id=:localidade_id AND data_hora=:data_hora
              AND estacao_id IS NULL AND fonte=:fonte FOR UPDATE'''), registro).scalars().all()
        if len(ids) > 1:
            raise ValueError('Duplicidade de leitura IQAir existente')
        if ids:
            registro['id'] = ids[0]
            conn.execute(text('UPDATE public.historico_qualidade_ar SET ' +
                              ','.join(c + '=:' + c for c in campos) + ' WHERE id=:id'), registro)
        else:
            colunas = ('localidade_id', 'data_hora', 'fonte') + campos
            conn.execute(text('INSERT INTO public.historico_qualidade_ar (' +
                              ','.join(colunas) + ') VALUES (' +
                              ','.join(':' + c for c in colunas) + ')'), registro)
        print('Leitura conferida:', linha['cidade'], linha['data_hora'],
              'IQA EUA', linha['aqi_usa'], 'IQA China', linha['aqi_cn'])


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gravar', action='store_true')
    parser.add_argument('--data-referencia', type=lambda v: datetime.fromisoformat(v).date(),
                        help='Airflow: execucoes antigas nao devem simular historico atual')
    args = parser.parse_args()
    hoje = datetime.now(timezone(timedelta(hours=-4))).date()
    if args.data_referencia is not None and args.data_referencia != hoje:
        print('Execucao fora do dia atual: IQAir sem historico liberado; nenhuma coleta ou carga.')
        return
    chave = os.environ.get('IQAIR_API_KEY') or dotenv_values(ROOT / '.env').get('IQAIR_API_KEY')
    if not chave:
        raise ValueError('Configure IQAIR_API_KEY')
    pasta = ROOT / 'dados' / 'iqair' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    pasta.mkdir(parents=True)
    parametros = dict(state='Rondonia', country='Brazil')
    cidades = consultar('cities', parametros, chave)
    (pasta / 'cidades_api.json').write_text(json.dumps(cidades, ensure_ascii=False, indent=2), encoding='utf-8')
    linhas = []
    for numero_cidade, item in enumerate(cidades, 1):
        time.sleep(13)  # Community: no maximo 5 chamadas/minuto, incluindo cities.
        cidade = item['city']
        try:
            dados = consultar('city', dict(parametros, city=cidade), chave)
        except SemLeitura:
            print('Fonte sem leitura:', cidade, '; lacuna preservada.')
            continue
        (pasta / ('cidade_' + str(numero_cidade) + '.json')).write_text(
            json.dumps(dados, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            linhas.append(transformar(dados, cidade))
        except SemLeitura:
            print('Fonte sem leitura:', cidade, '; lacuna preservada.')
    if linhas:
        with (pasta / 'leituras_cidades.csv').open('w', encoding='utf-8-sig', newline='') as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=list(linhas[0]))
            escritor.writeheader()
            escritor.writerows(linhas)
    print('Cidades com leitura exportada:', len(linhas))
    print('Destino:', pasta)
    if args.gravar and linhas:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        url = os.environ.get('QUALIDADE_AR_DATABASE_URL') or os.environ.get('DATABASE_URL') or dotenv_values(ROOT / '.env').get('DATABASE_URL')
        engine = create_engine(make_url(url).set(database='qualidade_ar'), connect_args={'connect_timeout': 10})
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL statement_timeout='30s'"))
                salvar(conn, linhas)
            print('Carga IQAir confirmada no banco qualidade_ar.')
        finally:
            engine.dispose()
    else:
        print('Sem carga no banco; sem extracao de historico ou estacoes individuais.')


if __name__ == '__main__':
    try:
        main()
    except Exception as erro:
        print('Falha na exportacao:', type(erro).__name__)
        raise SystemExit(1)
