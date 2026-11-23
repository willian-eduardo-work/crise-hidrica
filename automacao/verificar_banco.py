"""Inspeciona a estrutura ANA em transacao somente leitura, sem expor credenciais."""
import os
import argparse
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from automacao_media_diaria_nivel_2026 import (
    ESTACOES, SCHEMA_BANCO, calcular_media_diaria, extrair_codigo_estacao, formatar_dia,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coleta', type=Path, help='Compara as medias das planilhas com os valores gravados')
    parser.add_argument('--ano', type=int, default=date.today().year)
    args = parser.parse_args()
    if not 1900 <= args.ano <= 9999:
        parser.error('Ano invalido')
    ano = str(args.ano)
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            print('Autenticacao PostgreSQL confirmada; inspecao somente leitura.')
            for estacao in ESTACOES.values():
                tabela = estacao['tabela']
                colunas = conn.execute(text('SELECT column_name, data_type FROM information_schema.columns '
                    'WHERE table_schema=:schema AND table_name=:tabela ORDER BY ordinal_position'),
                    {'schema': SCHEMA_BANCO, 'tabela': tabela}).all()
                print(tabela, ':', ', '.join(f'{nome} ({tipo})' for nome, tipo in colunas
                      if nome in {'DIA', 'DIA_DATE', ano}) or 'COLUNAS ESPERADAS AUSENTES OU SEM ACESSO')
                if colunas:
                    total = conn.execute(text(f'SELECT count(*) FROM "{SCHEMA_BANCO}"."{tabela}"')).scalar_one()
                    print('Registros:', total)
                    for chave in ('DIA', 'DIA_DATE'):
                        if chave not in {nome for nome, _ in colunas}:
                            continue
                        duplicados = conn.execute(text(
                            f'SELECT "{chave}", count(*) FROM "{SCHEMA_BANCO}"."{tabela}" '
                            f'WHERE "{chave}" IS NOT NULL GROUP BY "{chave}" HAVING count(*) > 1 LIMIT 10')).all()
                        print('Duplicados em', chave, ':', duplicados)
                    if {'DIA_DATE', ano} <= {nome for nome, _ in colunas}:
                        resumo = conn.execute(text(
                            f'SELECT min("DIA_DATE"), max("DIA_DATE"), count("{ano}") '
                            f'FROM "{SCHEMA_BANCO}"."{tabela}"')).one()
                        print('Datas minima/maxima e valores preenchidos em', ano, ':', resumo)
            if args.coleta:
                arquivos = list(args.coleta.glob('5-*.xls*'))
                if not arquivos:
                    raise ValueError('Nenhuma planilha na pasta informada')
                conferidos = 0
                for arquivo in arquivos:
                    tabela = ESTACOES[extrair_codigo_estacao(arquivo.name)]['tabela']
                    medias = calcular_media_diaria(str(arquivo))
                    if medias.empty:
                        raise ValueError('Planilha sem medias')
                    for linha in medias.itertuples():
                        ano = str(linha.DIA_DATE.year)
                        valores = conn.execute(text(
                            f'SELECT "{ano}" FROM "{SCHEMA_BANCO}"."{tabela}" WHERE "DIA"=:dia'),
                            {'dia': formatar_dia(linha.DIA_DATE)}).scalars().all()
                        if len(valores) != 1 or valores[0] is None or abs(
                            float(str(valores[0]).replace(',', '.')) - linha.NIVEL_MEDIO
                        ) > 1e-8:
                            raise ValueError('Media gravada diverge da coleta')
                        conferidos += 1
                        print('Media conferida:', tabela, linha.DIA_DATE, valores[0])
                print('Total de medias conferidas:', conferidos)
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as erro:
        print('Falha na verificacao:', type(erro).__name__, '; confira acesso, credenciais e estrutura.')
        raise SystemExit(1)
