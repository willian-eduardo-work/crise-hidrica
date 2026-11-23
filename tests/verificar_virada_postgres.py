"""Teste real de virada em tabela temporaria, com rollback incondicional."""
import os
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'automacao'))
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import automacao_media_diaria_nivel_2026 as carga

def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 10})
    original = carga.SCHEMA_BANCO
    try:
        with engine.connect() as conn:
            transacao = conn.begin()
            try:
                conn.execute(text('CREATE TEMP TABLE teste_virada ("DIA" VARCHAR(20), "DIA_DATE" DATE) ON COMMIT DROP'))
                carga.SCHEMA_BANCO = 'pg_temp'
                session = Session(bind=conn)
                for ano in (2026, 2027, 2028):
                    carga.garantir_colunas(session, 'teste_virada', ano)
                for dt, valor in ((date(2026,1,1), 100), (date(2026,12,31), 200),
                                  (date(2027,1,1), 300), (date(2026,1,1), 101),
                                  (date(2028,2,29), 400), (date(2027,1,1), 300)):
                    carga.salvar_media_diaria(session, 'teste_virada', dt, valor)
                rows = conn.execute(text('SELECT "DIA", "DIA_DATE", "2026", "2027", "2028" FROM pg_temp.teste_virada')).all()
                assert len(rows) == 3, rows
                jan = next(r for r in rows if r[0]=='1-jan.')
                assert jan[1] == date(2027,1,1), jan
                assert float(jan[2].replace(',','.')) == 101, jan
                assert float(jan[3].replace(',','.')) == 300, jan
                assert next(r for r in rows if r[0]=='29-fev.')[4] is not None
                print('OK: virada, retroativo, idempotencia e 29/fev; nenhuma tabela de producao alterada.')
            finally:
                transacao.rollback()
    finally:
        carga.SCHEMA_BANCO = original
        engine.dispose()

if __name__ == '__main__':
    try:
        main()
    except Exception as erro:
        print('Teste falhou:', type(erro).__name__)
        raise SystemExit(1)
