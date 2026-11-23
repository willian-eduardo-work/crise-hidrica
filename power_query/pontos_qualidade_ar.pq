let
    Pontos = Odbc.Query("dsn=QualidadeDoAr", "SELECT * FROM public.bi_pontos_qualidade_ar")
in
    Pontos
