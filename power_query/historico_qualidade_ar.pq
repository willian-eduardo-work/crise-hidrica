let
    Historico = Odbc.Query("dsn=QualidadeDoAr", "SELECT * FROM public.bi_historico_qualidade_ar")
in
    Historico
