# coding: utf-8
"""Medias diarias ANA e carga no modelo PostgreSQL do painel.
Entrada: ANA_DATA_DIR ou dados/ana. Conexao: DATABASE_URL.
Destino anual determinado pelo ano de cada medicao, inclusive na virada.
"""

from __future__ import annotations

import os
import re
import glob
import logging
import unicodedata
from datetime import date
from typing import Optional, Dict, List

import pandas as pd


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    encoding="utf-8",
)

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

DIRETORIO_BASE = os.environ.get("ANA_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados", "ana"))

SCHEMA_BANCO = "dados_ANA"

ESTACOES: Dict[str, Dict[str, str]] = {
    "15560000": {
        "origem": "5",
        "nome": "jiparana",
        "tabela": "15560000_Ji_Parana",
    },
    "15400000": {
        "origem": "5",
        "nome": "portovelho",
        "tabela": "15400000_PORTO_VELHO",
    },
    "15430000": {
        "origem": "5",
        "nome": "ariquemes",
        "tabela": "15430000_ARIQUEMES",
    },
    "15250000": {
        "origem": "5",
        "nome": "guajara",
        "tabela": "15400000_GUAJARA_MIRIM",
    },
}

MESES_PT = {
    1: "jan.",
    2: "fev.",
    3: "mar.",
    4: "abr.",
    5: "mai.",
    6: "jun.",
    7: "jul.",
    8: "ago.",
    9: "set.",
    10: "out.",
    11: "nov.",
    12: "dez.",
}


def remover_acentos(texto: str) -> str:
    """Remove acentos usando apenas biblioteca padrão do Python."""
    texto_normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto_normalizado if not unicodedata.combining(c))


def normalizar_texto(valor: object) -> str:
    """Remove acentos, coloca em minúsculo e reduz espaços."""
    if pd.isna(valor):
        return ""

    texto = str(valor).strip()
    texto = remover_acentos(texto).lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def formatar_dia(data_ref: date) -> str:
    """Formata a coluna DIA conforme o padrão antigo: 8-jun."""
    return f"{data_ref.day}-{MESES_PT[data_ref.month]}"


def verificar_arquivo(caminho_arquivo: str) -> bool:
    """Verifica se o arquivo existe, é arquivo e não está vazio."""
    if not os.path.exists(caminho_arquivo):
        logger.warning(f"Arquivo não encontrado: {caminho_arquivo}")
        return False

    if not os.path.isfile(caminho_arquivo):
        logger.warning(f"O caminho não é um arquivo: {caminho_arquivo}")
        return False

    if os.path.getsize(caminho_arquivo) == 0:
        logger.warning(f"Arquivo vazio: {caminho_arquivo}")
        return False

    return True


def extrair_codigo_estacao(nome_arquivo: str) -> Optional[str]:
    'Extrai o código da estação a partir do nome do arquivo.'
    match = re.match(r"5-(\d+)", nome_arquivo)

    if not match:
        return None

    return match.group(1)


def converter_numero(valor: object) -> Optional[float]:
    'Converte valores numéricos vindos da planilha.'
    if pd.isna(valor):
        return None

    texto = str(valor).strip()

    if texto == "":
        return None

    # Remove espaços e caracteres invisíveis.
    texto = texto.replace("\xa0", "").replace(" ", "")

    # Caso brasileiro: 1.234,56
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    # Caso brasileiro simples: 968,40
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


def localizar_linha_cabecalho(df: pd.DataFrame) -> Optional[int]:
    'Localiza a linha que contém o cabeçalho da tabela.'
    for idx in range(len(df)):
        valor_primeira_coluna = normalizar_texto(df.iloc[idx, 0])

        if valor_primeira_coluna in {"data/hora", "data hora", "data"}:
            return idx

    return None


def localizar_coluna_nivel(colunas: List[object]) -> Optional[object]:
    'Localiza a coluna de Nível adotado (cm).'
    for coluna in colunas:
        nome_coluna = normalizar_texto(coluna)

        if "nivel" in nome_coluna and "adotado" in nome_coluna:
            return coluna

    return None


def ler_planilha_ana(caminho_arquivo: str) -> pd.DataFrame:
    'Lê a planilha da ANA.'
    try:
        tabelas = pd.read_html(caminho_arquivo, encoding="latin1")
    except Exception:
        tabelas = pd.read_html(caminho_arquivo)

    if not tabelas:
        raise ValueError("Nenhuma tabela foi encontrada no arquivo.")

    df_raw = tabelas[0]

    linha_cabecalho = localizar_linha_cabecalho(df_raw)

    if linha_cabecalho is None:
        raise ValueError("Não foi possível localizar a linha de cabeçalho com 'Data/Hora'.")

    cabecalho = df_raw.iloc[linha_cabecalho].tolist()
    df = df_raw.iloc[linha_cabecalho + 1:].copy()
    df.columns = cabecalho

    df = df.dropna(axis=1, how="all")

    coluna_data = df.columns[0]

    coluna_nivel = localizar_coluna_nivel(list(df.columns))

    if coluna_nivel is None:
        # Exportacao ANA valida, com cabecalho e mensagem explicita de ausencia.
        conteudo = ' '.join(normalizar_texto(v) for v in df_raw.iloc[:, 0])
        if 'nao ha registro para esta estacao no periodo selecionado' in conteudo:
            return pd.DataFrame(columns=['DATA_HORA', 'NIVEL_ADOTADO_CM', 'DIA_DATE'])
        raise ValueError("Não foi possível localizar a coluna 'Nível adotado (cm)'.")

    df_processado = df[[coluna_data, coluna_nivel]].copy()
    df_processado.columns = ["DATA_HORA", "NIVEL_ADOTADO_CM"]

    df_processado["DATA_HORA"] = pd.to_datetime(
        df_processado["DATA_HORA"],
        errors="coerce",
        dayfirst=True,
    )

    df_processado["NIVEL_ADOTADO_CM"] = df_processado["NIVEL_ADOTADO_CM"].apply(converter_numero)

    df_processado = df_processado.dropna(subset=["DATA_HORA", "NIVEL_ADOTADO_CM"])

    df_processado["DIA_DATE"] = df_processado["DATA_HORA"].dt.date

    return df_processado


def calcular_media_diaria(caminho_arquivo: str) -> pd.DataFrame:
    'Calcula a média diária do Nível adotado (cm) usando a primeira coluna como base.'
    df = ler_planilha_ana(caminho_arquivo)

    if df.empty:
        return pd.DataFrame(columns=["DIA_DATE", "NIVEL_MEDIO", "QTD_REGISTROS"])

    media_diaria = (
        df.groupby("DIA_DATE", as_index=False)
        .agg(
            NIVEL_MEDIO=("NIVEL_ADOTADO_CM", "mean"),
            QTD_REGISTROS=("NIVEL_ADOTADO_CM", "count"),
        )
        .sort_values("DIA_DATE")
    )

    return media_diaria


def criar_sessao():
    """Cria conexão com o banco de dados."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if not DATABASE_URL:
        raise ValueError("Configure DATABASE_URL no ambiente ou use pipeline_diario.py")
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    return engine, Session()


def garantir_colunas(session, tabela: str, ano: int) -> None:
    'Garante que as colunas necessárias existam na tabela.'
    from sqlalchemy import text

    if not 1900 <= int(ano) <= 9999:
        raise ValueError('Ano invalido')
    sql = f"""
    CREATE TABLE IF NOT EXISTS "{SCHEMA_BANCO}"."{tabela}" (
        "DIA" VARCHAR(20), "DIA_DATE" DATE
    );
    ALTER TABLE "{SCHEMA_BANCO}"."{tabela}"
    ADD COLUMN IF NOT EXISTS "{ano}" VARCHAR(20);

    ALTER TABLE "{SCHEMA_BANCO}"."{tabela}"
    ADD COLUMN IF NOT EXISTS "DIA_DATE" DATE;
    """

    session.execute(text(sql))


def salvar_media_diaria(session, tabela: str, data_ref: date, nivel_medio: float) -> None:
    'Salva a média diária no banco.'
    from sqlalchemy import text

    dia_str = formatar_dia(data_ref)
    ano = str(data_ref.year)
    nivel_formatado = f"{float(nivel_medio):.10f}".replace(".", ",")

    sql_update_por_data = f"""
    UPDATE "{SCHEMA_BANCO}"."{tabela}"
    SET
        "{ano}" = :nivel_formatado,
        "DIA" = :dia_str,
        "DIA_DATE" = :data_ref
    WHERE "DIA_DATE" = :data_ref;
    """

    resultado = session.execute(
        text(sql_update_por_data),
        {
            "nivel_formatado": nivel_formatado,
            "dia_str": dia_str,
            "data_ref": data_ref,
        },
    )

    if resultado.rowcount and resultado.rowcount > 0:
        logger.info(
            f"{tabela} | {data_ref} | atualizado por DIA_DATE | média: {nivel_formatado}"
        )
        return

    sql_update_por_dia = f"""
    UPDATE "{SCHEMA_BANCO}"."{tabela}"
    SET
        "{ano}" = :nivel_formatado,
        "DIA_DATE" = GREATEST("DIA_DATE", :data_ref)
    WHERE "DIA" = :dia_str;
    """

    resultado = session.execute(
        text(sql_update_por_dia),
        {
            "nivel_formatado": nivel_formatado,
            "dia_str": dia_str,
            "data_ref": data_ref,
        },
    )

    if resultado.rowcount and resultado.rowcount > 0:
        logger.info(
            f"{tabela} | {data_ref} | atualizado por DIA | média: {nivel_formatado}"
        )
        return

    sql_insert = f"""
    INSERT INTO "{SCHEMA_BANCO}"."{tabela}" ("DIA", "DIA_DATE", "{ano}")
    VALUES (:dia_str, :data_ref, :nivel_formatado);
    """

    session.execute(
        text(sql_insert),
        {
            "dia_str": dia_str,
            "data_ref": data_ref,
            "nivel_formatado": nivel_formatado,
        },
    )

    logger.info(
        f"{tabela} | {data_ref} | novo registro criado | média: {nivel_formatado}"
    )


def processar_arquivo(engine, session, caminho_arquivo: str) -> None:
    'Processa um arquivo:'
    nome_arquivo = os.path.basename(caminho_arquivo)
    codigo_estacao = extrair_codigo_estacao(nome_arquivo)

    if not codigo_estacao:
        logger.warning(f"Código da estação não encontrado no arquivo: {nome_arquivo}")
        return

    estacao = ESTACOES.get(codigo_estacao)

    if not estacao:
        logger.warning(
            f"Estação com código {codigo_estacao} não mapeada. Arquivo ignorado: {nome_arquivo}"
        )
        return

    tabela = estacao["tabela"]

    logger.info(f"Processando arquivo: {nome_arquivo}")
    logger.info(f"Estação identificada: {estacao['nome']} | Tabela: {tabela}")

    media_diaria = calcular_media_diaria(caminho_arquivo)

    if media_diaria.empty:
        logger.warning(f"Nenhuma média diária calculada para o arquivo: {nome_arquivo}")
        return

    # DDL e dados usam a mesma transacao: outra conexao poderia aguardar
    # os locks desta propria carga ao processar varios arquivos da estacao.
    for ano in sorted({d.year for d in media_diaria['DIA_DATE']}):
        garantir_colunas(session, tabela, ano)

    logger.info(f"Dias encontrados no arquivo: {len(media_diaria)}")

    for _, linha in media_diaria.iterrows():
        data_ref = linha["DIA_DATE"]
        nivel_medio = linha["NIVEL_MEDIO"]
        qtd_registros = int(linha["QTD_REGISTROS"])

        salvar_media_diaria(session, tabela, data_ref, nivel_medio)

        logger.info(
            f"{nome_arquivo} | {data_ref} | registros usados: {qtd_registros} | "
            f"média nível adotado: {nivel_medio:.4f}"
        )


def processar_diretorio(diretorio_base: str) -> None:
    'Processa todos os arquivos 5-*.xls* encontrados no diretório informado.'
    if not os.path.exists(diretorio_base):
        raise FileNotFoundError(f"Diretório não encontrado: {diretorio_base}")

    arquivos = glob.glob(os.path.join(diretorio_base, "5-*.xls*"))

    arquivos_validos = [
        arquivo for arquivo in arquivos
        if verificar_arquivo(arquivo)
    ]

    if not arquivos_validos:
        logger.warning(f"Nenhum arquivo válido encontrado em: {diretorio_base}")
        return

    logger.info(f"Arquivos encontrados: {len(arquivos_validos)}")

    engine, session = criar_sessao()

    try:
        for arquivo in arquivos_validos:
            processar_arquivo(engine, session, arquivo)

        session.commit()
        logger.info("Processamento concluído e alterações salvas no banco.")

    except Exception as erro:
        session.rollback()
        logger.error('Erro durante o processamento (%s). Alteracoes desfeitas.', type(erro).__name__)
        raise

    finally:
        session.close()
        engine.dispose()


def main():
    logger.info("Iniciando automação de média diária do Nível Adotado (cm).")
    processar_diretorio(DIRETORIO_BASE)
    logger.info("Automação finalizada.")


if __name__ == "__main__":
    main()
