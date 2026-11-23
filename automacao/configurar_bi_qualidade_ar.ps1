param([int]$Porta, [switch]$Aplicar)
$ErrorActionPreference = 'Stop'
$raizProjeto = Split-Path $PSScriptRoot -Parent
Add-Type -Path 'C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.Tabular.dll'
$biServer = New-Object Microsoft.AnalysisServices.Tabular.Server

function Adicionar-Coluna($tabela, $nome, $tipo) {
    if (-not $tabela.Columns.Contains($nome)) {
        $coluna = New-Object Microsoft.AnalysisServices.Tabular.DataColumn
        $coluna.Name = $nome
        $coluna.SourceColumn = $nome
        $coluna.DataType = [Microsoft.AnalysisServices.Tabular.DataType]::$tipo
        $tabela.Columns.Add($coluna)
    }
}

function Adicionar-Tabela($modelo, $nome, $arquivo, $colunas) {
    if ($modelo.Tables.Contains($nome)) { throw "Tabela ja existe: $nome" }
    $tabela = New-Object Microsoft.AnalysisServices.Tabular.Table
    $tabela.Name = $nome
    $particao = New-Object Microsoft.AnalysisServices.Tabular.Partition
    $particao.Name = $nome
    $particao.Mode = [Microsoft.AnalysisServices.Tabular.ModeType]::Import
    $origem = New-Object Microsoft.AnalysisServices.Tabular.MPartitionSource
    $origem.Expression = Get-Content -LiteralPath (Join-Path $raizProjeto $arquivo) -Raw
    $particao.Source = $origem
    $tabela.Partitions.Add($particao)
    foreach ($nomeColuna in $colunas.Keys) { Adicionar-Coluna $tabela $nomeColuna $colunas[$nomeColuna] }
    $modelo.Tables.Add($tabela)
    return $tabela
}

try {
    $biServer.Connect("Data Source=localhost:$Porta")
    if ($biServer.Databases.Count -ne 1) { throw 'Esperado um unico modelo aberto nesta porta' }
    $biDb = $biServer.Databases[0]
    $modelo = $biDb.Model
    if (-not $modelo.Tables.Contains('historico_qualidade_ar')) { throw 'Modelo nao corresponde ao painel' }
    $historico = $modelo.Tables['historico_qualidade_ar']
    if ($historico.Partitions.Count -ne 1 -or $historico.Partitions[0].Source.Expression -notmatch 'QualidadeDoAr') {
        throw 'Fonte do historico diferente da esperada'
    }
    if (-not $Aplicar) {
        Write-Output 'Modelo confirmado. Aplicacao adicionara pontos, calendario e relacionamentos; preservara medidas e visuais.'
        return
    }
    $backupDir = Join-Path $raizProjeto 'dados/backups_qualidade_ar'
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $instanteBackup = Get-Date -Format 'yyyyMMdd_HHmmss_ffff'
    $arquivoBackup = Join-Path $backupDir "modelo_$instanteBackup.bim"
    $opcoes = New-Object Microsoft.AnalysisServices.Tabular.SerializeOptions
    $modeloJson = [Microsoft.AnalysisServices.Tabular.JsonSerializer]::SerializeDatabase($biDb, $opcoes)
    [System.IO.File]::WriteAllText($arquivoBackup, $modeloJson)
    Copy-Item -LiteralPath (Join-Path $raizProjeto 'painelDeMonitoramentoCriseHidrica.pbix') -Destination (Join-Path $backupDir "painel_$instanteBackup.pbix")

    $pontos = Adicionar-Tabela $modelo 'pontos_qualidade_ar' 'power_query/pontos_qualidade_ar.pq' ([ordered]@{
        ponto_id='String';localidade_id='Int64';estacao_id='Int64';cidade='String';estado='String';pais='String';tipo='String';estacao='String';ponto='String';latitude='Double';longitude='Double';fonte_cadastro='String';status_operacional='String'
    })
    $calendario = Adicionar-Tabela $modelo 'calendario_qualidade_ar' 'power_query/calendario_qualidade_ar.pq' ([ordered]@{
        data='DateTime';ano='Int64';mes_num='Int64';dia='Int64'
    })
    $historico.Partitions[0].Source.Expression = Get-Content -LiteralPath (Join-Path $raizProjeto 'power_query/historico_qualidade_ar.pq') -Raw
    Adicionar-Coluna $historico 'ponto_id' 'String'
    Adicionar-Coluna $historico 'estacao_id' 'Int64'
    Adicionar-Coluna $historico 'data' 'DateTime'
    Adicionar-Coluna $historico 'tipo_medicao' 'String'
    foreach ($definicao in @(@('ponto_id', $pontos, 'ponto_id'), @('data', $calendario, 'data'))) {
        $relacao = New-Object Microsoft.AnalysisServices.Tabular.SingleColumnRelationship
        $relacao.Name = 'qualidade_ar_' + $definicao[0]
        $relacao.FromColumn = $historico.Columns[$definicao[0]]
        $relacao.ToColumn = $definicao[1].Columns[$definicao[2]]
        $relacao.FromCardinality = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::Many
        $relacao.ToCardinality = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::One
        $relacao.CrossFilteringBehavior = [Microsoft.AnalysisServices.Tabular.CrossFilteringBehavior]::OneDirection
        $relacao.IsActive = $true
        $modelo.Relationships.Add($relacao)
    }
    $medidas = [ordered]@{
        'IQA_EUA_por_Ponto' = 'IF(HASONEVALUE(pontos_qualidade_ar[tipo]), AVERAGE(historico_qualidade_ar[aqi_usa]), CALCULATE(AVERAGE(historico_qualidade_ar[aqi_usa]), KEEPFILTERS(pontos_qualidade_ar[tipo] = "cidade")))'
        'IQA_China_por_Ponto' = 'IF(HASONEVALUE(pontos_qualidade_ar[tipo]), AVERAGE(historico_qualidade_ar[aqi_cn]), CALCULATE(AVERAGE(historico_qualidade_ar[aqi_cn]), KEEPFILTERS(pontos_qualidade_ar[tipo] = "cidade")))'
        'Leituras_por_Ponto' = 'COUNTROWS(historico_qualidade_ar)'
        'Ultima_Leitura_por_Ponto' = 'MAX(historico_qualidade_ar[data_hora])'
        'Selecao_do_Ponto' = 'SELECTEDVALUE(pontos_qualidade_ar[ponto], "Selecione uma cidade ou estacao")'
        'Temperatura_por_Ponto' = 'IF(HASONEVALUE(pontos_qualidade_ar[ponto_id]), VAR Instante = MAX(historico_qualidade_ar[data_hora]) RETURN CALCULATE(AVERAGE(historico_qualidade_ar[temperatura]), historico_qualidade_ar[data_hora] = Instante))'
    }
    foreach ($nomeMedida in $medidas.Keys) {
        if ($historico.Measures.Contains($nomeMedida)) { throw "Medida ja existe: $nomeMedida" }
        $medida = New-Object Microsoft.AnalysisServices.Tabular.Measure
        $medida.Name = $nomeMedida
        $medida.Expression = $medidas[$nomeMedida]
        $medida.DisplayFolder = 'Monitoramento por ponto'
        $historico.Measures.Add($medida)
    }
    $historico.Measures['Ultima_Leitura_por_Ponto'].FormatString = 'dd/MM/yyyy HH:mm'
    $calendario.Columns['data'].FormatString = 'dd/MM/yyyy'
    # Um unico SaveChanges para metadados e processamento: falhas nao devem
    # deixar dimensoes parcialmente aplicadas no modelo aberto.
    $pontos.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $calendario.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $historico.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
    $modelo.SaveChanges() | Out-Null
    Write-Output "Modelo atualizado na sessao aberta. Backup: $arquivoBackup"
    Write-Output 'Salvar o PBIX no Power BI para persistir as mudancas no arquivo.'
} finally { $biServer.Dispose() }
