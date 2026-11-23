let
    Calendario = Odbc.Query("dsn=QualidadeDoAr", "SELECT * FROM public.bi_calendario_qualidade_ar")
in
    Calendario
