import os
import pandas as pd



class auxFunctions:
    # -------------------------
    # FUNÇÕES AUXILIARES
    # -------------------------
    @staticmethod
    def carregar_dados(con, parquet_file, n_rows=None, sample=False, station=None):
        """
        Carrega os dados a partir de um arquivo Parquet usando DuckDB.bafala 
        Cria a tabela solar_raw no banco em memória.

        Args:
            con: conexão DuckDB
            parquet_file: caminho do arquivo .parquet
            n_rows: número de linhas para carregar (None = todas)
            sample: se True, seleciona linhas aleatórias em vez das primeiras
            station: filtra apenas uma estação pelo acronym
        """
        if not os.path.exists(parquet_file):
            raise FileNotFoundError(f"Arquivo {parquet_file} não encontrado.")

        filtro = f"WHERE acronym = '{station}'" if station else ""

        if n_rows:
            if sample:
                query = f"""
                    CREATE OR REPLACE TABLE solar_raw AS
                    SELECT * FROM read_parquet('{parquet_file}')
                    {filtro}
                    USING SAMPLE {n_rows} ROWS
                """
            else:
                query = f"""
                    CREATE OR REPLACE TABLE solar_raw AS
                    SELECT * FROM read_parquet('{parquet_file}')
                    {filtro}
                    LIMIT {n_rows}
                """
        else:
            query = f"""
                CREATE OR REPLACE TABLE solar_raw AS
                SELECT * FROM read_parquet('{parquet_file}')
                {filtro}
            """

        con.execute(query)
        print(f"Tabela 'solar_raw' criada a partir de {parquet_file} {f'para {station}' if station else ''}")
        

    @staticmethod
    def load_station_metadata():
        """Carrega metadados (station, lat, lon) do CSV"""
        location_csv = os.path.expanduser("/home/daniel/inpe/SolterData/INPE_SONDA/sonda_validation/INPESONDA_stations.csv")
        if not os.path.exists(location_csv):
            raise FileNotFoundError("Arquivo 'INPESONDA_stations.csv' não encontrado.")

        df_locations = pd.read_csv(location_csv)
        df_locations['station_normalized'] = df_locations['station'].astype(str).str.strip().str.upper()        
        return df_locations[['station', 'latitude', 'longitude', 'station_normalized']]


    @staticmethod
    def load_normais_climaticas():
        """Carrega as normais climáticas a partir de um CSV"""
        normais_csv = os.path.expanduser("/home/daniel/inpe/SolterData/INPE_SONDA/sonda_validation/INPESONDA_normais.csv")
        if not os.path.exists(normais_csv):
            raise FileNotFoundError(f"Arquivo 'INPESONDA_normais.csv' não encontrado.")

        df_normais = pd.read_csv(normais_csv, sep=';')

        df_normais["acronym"] = df_normais["acronym"].astype(str).str.strip().str.upper()
        numeric_cols = ["tp_min", "tp_max", "press_min", "press_max", "rain_max"]
        for col in numeric_cols:
            if col in df_normais.columns:
                df_normais[col] = pd.to_numeric(df_normais[col], errors="coerce")        
        return df_normais

    @staticmethod
    def preprocess_conversion_data_fill_time(con, tabela_origem, tabela_destino, freq="10min"):
        col_info = con.execute(f'PRAGMA table_info("{tabela_origem}")').fetch_df()
        colunas = col_info['name'].tolist()
        timestamp_col = next((col for col in colunas if "time" in col.lower()), None)
        if timestamp_col is None:
            raise ValueError("Nenhuma coluna de timestamp encontrada na tabela!")
        colunas_para_cast = [col for col in colunas if col not in [timestamp_col, "acronym"]]
        select_fixas = f'CAST("{timestamp_col}" AS TIMESTAMP) AS "{timestamp_col}", "acronym"'
        select_cast = ", ".join([f'CAST("{col}" AS DOUBLE) AS "{col}"' for col in colunas_para_cast])
        select_final = select_fixas + (", " + select_cast if select_cast else "")
        temp_table = tabela_destino + "_temp"
        con.execute(f"""
            CREATE OR REPLACE TABLE "{temp_table}" AS
            SELECT {select_final}
            FROM "{tabela_origem}";
        """)
        df = con.execute(f'SELECT * FROM "{temp_table}" ORDER BY "{timestamp_col}"').df()
        full_range = pd.date_range(start=df[timestamp_col].min(), 
                                end=df[timestamp_col].max(),
                                freq=freq)
        df_full = pd.DataFrame({timestamp_col: full_range})
        df_full = df_full.merge(df, on=timestamp_col, how="left")
        df_full["valid"] = ~df_full[colunas_para_cast].isna().any(axis=1)
        con.register("df_full_temp", df_full)
        con.execute(f'CREATE OR REPLACE TABLE "{tabela_destino}" AS SELECT * FROM df_full_temp')
        return df_full    