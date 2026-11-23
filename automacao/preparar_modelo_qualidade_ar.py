"""Valida a migracao em rollback; --aplicar confirma cidades/estacoes/views.

Nao insere medicoes. Salva copia dos dados existentes antes de aplicar.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def snapshot(conn):
    return {t: [dict(r) for r in conn.execute(text(
        'SELECT * FROM public.' + t + ' ORDER BY id')).mappings()]
        for t in ('localidade', 'historico_qualidade_ar')}


def validar(conn, antes):
    depois = snapshot(conn)
    # A unica coluna nova no historico deve permanecer nula para dados legados.
    for r in depois['historico_qualidade_ar']:
        if 'estacao_id' not in antes['historico_qualidade_ar'][0]:
            if r.pop('estacao_id') is not None:
                raise ValueError('Migracao atribuiu estacao a dado legado')
    if antes != depois:
        raise ValueError('Migracao alterou os registros existentes')
    total = conn.execute(text('SELECT count(*) FROM public.bi_historico_qualidade_ar')).scalar_one()
    if total != len(antes['historico_qualidade_ar']):
        raise ValueError('View duplicou ou perdeu registros')
    invalidos = conn.execute(text('''SELECT count(*) FROM public.bi_historico_qualidade_ar h
        LEFT JOIN public.bi_pontos_qualidade_ar p USING (ponto_id)
        WHERE h.ponto_id IS NOT NULL AND p.ponto_id IS NULL''')).scalar_one()
    if invalidos:
        raise ValueError('Historico sem dimensao correspondente')
    print('Registros preservados:', total)
    print('Pontos cadastrados:', conn.execute(text(
        'SELECT count(*) FROM public.bi_pontos_qualidade_ar')).scalar_one())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aplicar', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    url = make_url(os.environ['DATABASE_URL']).set(database='qualidade_ar')
    engine = create_engine(url, connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as conn:
            transacao = conn.begin()
            try:
                conn.execute(text("SET LOCAL lock_timeout='5s'"))
                conn.execute(text("SET LOCAL statement_timeout='30s'"))
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('modelo_qualidade_ar'))"))
                conn.execute(text('LOCK TABLE public.localidade, public.historico_qualidade_ar IN SHARE ROW EXCLUSIVE MODE'))
                antes = snapshot(conn)
                if args.aplicar:
                    pasta = ROOT / 'dados' / 'backups_qualidade_ar'
                    pasta.mkdir(parents=True, exist_ok=True)
                    arquivo = pasta / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '.json')
                    arquivo.write_text(json.dumps(antes, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
                conn.exec_driver_sql((ROOT / 'automacao/modelo_qualidade_ar.sql').read_text(encoding='utf-8'))
                validar(conn, antes)
                if args.aplicar:
                    transacao.commit()
                    print('Estrutura aplicada. Backup:', arquivo)
                else:
                    # Reexecucao nao deve duplicar estacoes nem alterar leituras.
                    pontos = conn.execute(text('SELECT count(*) FROM public.bi_pontos_qualidade_ar')).scalar_one()
                    conn.exec_driver_sql((ROOT / 'automacao/modelo_qualidade_ar.sql').read_text(encoding='utf-8'))
                    validar(conn, antes)
                    if pontos != conn.execute(text('SELECT count(*) FROM public.bi_pontos_qualidade_ar')).scalar_one():
                        raise ValueError('Migracao nao e idempotente')
                    transacao.rollback()
                    print('Validacao concluida com rollback; nenhuma alteracao persistida.')
            except Exception:
                transacao.rollback()
                raise
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as erro:
        print('Falha na migracao:', type(erro).__name__)
        raise SystemExit(1)
