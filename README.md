# Monitoramento da Crise Hídrica

Coleta de níveis dos rios em Rondônia, cálculo de médias diárias e carga no PostgreSQL. As estações monitoradas são Ji-Paraná, Porto Velho, Ariquemes e Guajará-Mirim. O projeto também coleta as leituras atuais de qualidade do ar que a API da IQAir disponibiliza para cidades de Rondônia.

- [Telemetria da ANA](https://www.snirh.gov.br/hidrotelemetria) e [séries históricas do HidroWeb](https://www.snirh.gov.br/hidroweb/serieshistoricas)
- [API de qualidade do ar da IQAir](https://www.iqair.com/air-pollution-data-api) e [termos da IQAir](https://www.iqair.com/legal/terms-conditions)

## Executar localmente

Use Python 3.9 ou superior e Chrome ou Chromium. A partir da raiz do projeto, no PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe automacao/pipeline_diario.py --somente-coleta --dias 1
```

Para gravar as médias diárias, crie um banco PostgreSQL `crise_hidrica`, ajuste `DATABASE_URL` no `.env` e execute o pipeline sem `--somente-coleta`. Conecte-se a esse banco com o usuário da aplicação e execute `CREATE SCHEMA IF NOT EXISTS "dados_ANA";` antes da primeira carga. Esse usuário precisa poder criar tabelas e escrever nesse esquema. Os arquivos coletados são gravados localmente em `dados/`.

```powershell
.\.venv\Scripts\python.exe automacao/pipeline_diario.py --dias 1
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Qualidade do ar

Configure sua chave `IQAIR_API_KEY` no `.env` e execute `python automacao/inventario_iqair.py` no ambiente virtual para exportar a leitura atual. A cobertura depende da chave e das localidades retornadas pela API. O cadastro opcional das estações de Porto Velho é preenchido pelo SQL quando a localidade municipal já existe. Para preenchê-lo em um banco novo, reaplique o modelo após a primeira carga de Porto Velho.

Para usar `--gravar`, crie também um banco `qualidade_ar` no mesmo servidor e aplique `automacao/base_qualidade_ar.sql` seguido de `automacao/modelo_qualidade_ar.sql` no banco novo. No PostgreSQL, execute os dois arquivos com `psql --single-transaction -v ON_ERROR_STOP=1 -d qualidade_ar -f automacao/base_qualidade_ar.sql -f automacao/modelo_qualidade_ar.sql`. Em uma base legada compatível, `python automacao/preparar_modelo_qualidade_ar.py` valida a migração com rollback e `--aplicar` grava a migração.

## Airflow e Power BI

- A [DAG](airflow/dags/crise_hidrica_ana_diaria.py) usa Bash e Airflow 2.10. Configure `CRISE_HIDRICA_HOME` para o caminho local do projeto e, se necessário, `CRISE_HIDRICA_PYTHON` para o Python do ambiente virtual. Configure as conexões `crise_hidrica_postgres` e `qualidade_ar_iqair`, com a chave da IQAir no campo de senha da segunda conexão. A ANA roda às 07h e a IQAir às 07h, 13h e 19h no fuso de Manaus. A recuperação de execuções anteriores é opcional e usa `CRISE_HIDRICA_CATCHUP=1` e `CRISE_HIDRICA_START_DATE`.
- Em [power_query/](power_query/) estão as consultas M e a medida DAX. Configure os DSNs ODBC `PostgreSQL35W` e `QualidadeDoAr` para os bancos correspondentes. A consulta de Cacoal usa o parâmetro de texto `CaminhoPlanilhaCacoal`, que deve apontar para uma planilha compatível. As consultas de Porto Velho e Cacoal incluem tratamentos específicos do painel original.