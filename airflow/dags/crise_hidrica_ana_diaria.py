"""Airflow 2.2–2.11: coleta ANA e carga PostgreSQL às 07h de Manaus."""
from datetime import timedelta
import os
from pathlib import Path

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator


RAIZ = Path(os.environ.get('CRISE_HIDRICA_HOME', '/usr/local/airflow/include/crise_hidrica'))
PYTHON = os.environ.get('CRISE_HIDRICA_PYTHON', str(RAIZ / '.venv/bin/python'))
REFERENCIA = "{{ data_interval_end.in_timezone('America/Manaus').strftime('%Y-%m-%d') }}"

with DAG(
    dag_id='crise_hidrica_ana_diaria',
    description='ANA: quatro estacoes do dia anterior; IQAir: cidades disponiveis de RO, leitura atual',
    start_date=pendulum.parse(os.environ.get('CRISE_HIDRICA_START_DATE', '2026-11-09T07:00:00-04:00')).in_timezone('America/Manaus'),
    schedule_interval='0 7,13,19 * * *',
    catchup=os.environ.get('CRISE_HIDRICA_CATCHUP') == '1',
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=3),
    is_paused_upon_creation=True,
    default_args={
        'owner': 'monitoramento',
        'depends_on_past': False,
        'retries': 2,
        'retry_delay': timedelta(minutes=15),
        'execution_timeout': timedelta(minutes=40),
    },
    tags=['crise-hidrica', 'ana', 'qualidade-ar'],
    doc_md='07h, 13h e 19h (America/Manaus). ANA somente as 07h ou manual. IQAir nas tres execucoes; sem retroativos. Lacunas preservadas.',
) as dag:
    coletar_e_carregar = BashOperator(
        task_id='coletar_validar_e_carregar',
        bash_command='''
set -euo pipefail
if [ "$ANA_EXECUTAR" != "1" ]; then
    echo "ANA: coleta diaria reservada as 07h; tarefa ignorada neste horario."
    exit 99
fi
cd "$CRISE_HIDRICA_HOME"
test -x "$CRISE_HIDRICA_PYTHON"
test -f automacao/pipeline_diario.py
exec "$CRISE_HIDRICA_PYTHON" automacao/pipeline_diario.py \
    --dias 1 --data-referencia "$PIPELINE_DATA_REFERENCIA" \
    --estacoes 15560000 15400000 15430000 15250000
''',
        append_env=True,
        skip_on_exit_code=99,
        env={
            'CRISE_HIDRICA_HOME': str(RAIZ),
            'CRISE_HIDRICA_PYTHON': PYTHON,
            'PIPELINE_DATA_REFERENCIA': REFERENCIA,
            'ANA_EXECUTAR': "{{ '1' if dag_run.run_type == 'manual' or data_interval_end.in_timezone('America/Manaus').hour == 7 else '0' }}",
            'DATABASE_URL': "{{ conn.crise_hidrica_postgres.get_uri().replace('postgres://', 'postgresql+psycopg2://', 1) }}",
            'ANA_HEADLESS': '1',
            'TZ': 'America/Manaus',
        },
        do_xcom_push=False,
    )

    # Independente da ANA: indisponibilidade do portal nao impede o ar atual.
    # O coletor ignora datas antigas do catchup, pois a chave nao fornece historico.
    coletar_qualidade_ar = BashOperator(
        task_id='coletar_qualidade_ar_iqair',
        bash_command='''
set -euo pipefail
cd "$CRISE_HIDRICA_HOME"
exec "$CRISE_HIDRICA_PYTHON" automacao/inventario_iqair.py \\
    --gravar --data-referencia "$PIPELINE_DATA_REFERENCIA"
''',
        append_env=True,
        env={
            'CRISE_HIDRICA_HOME': str(RAIZ),
            'CRISE_HIDRICA_PYTHON': PYTHON,
            'PIPELINE_DATA_REFERENCIA': REFERENCIA,
            'DATABASE_URL': "{{ conn.crise_hidrica_postgres.get_uri().replace('postgres://', 'postgresql+psycopg2://', 1) }}",
            'IQAIR_API_KEY': '{{ conn.qualidade_ar_iqair.password }}',
            'TZ': 'America/Manaus',
        },
        do_xcom_push=False,
    )
