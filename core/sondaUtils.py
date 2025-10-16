import os
import duckdb
import gc
import time
from datetime import datetime, timedelta
import pandas as pd
from core.sondaValidator import SolarimetricValidator, MeteoValidator


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
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        location_csv = os.path.join(project_root, "data", "metadata", "INPESONDA_stations.csv")
        
        # Fallback to current directory if not found in data/metadata
        if not os.path.exists(location_csv):
            location_csv = os.path.join(project_root, "INPESONDA_stations.csv")
        
        if not os.path.exists(location_csv):
            raise FileNotFoundError(f"Arquivo 'INPESONDA_stations.csv' não encontrado. Procurou em: {location_csv}")

        df_locations = pd.read_csv(location_csv)
        df_locations['station_normalized'] = df_locations['station'].astype(str).str.strip().str.upper()        
        return df_locations[['station', 'latitude', 'longitude', 'station_normalized']]


    @staticmethod
    def load_normais_climaticas():
        """Carrega as normais climáticas a partir de um CSV"""
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        normais_csv = os.path.join(project_root, "data", "metadata", "INPESONDA_normais.csv")
        
        # Fallback to current directory if not found in data/metadata
        if not os.path.exists(normais_csv):
            normais_csv = os.path.join(project_root, "INPESONDA_normais.csv")
        
        if not os.path.exists(normais_csv):
            raise FileNotFoundError(f"Arquivo 'INPESONDA_normais.csv' não encontrado. Procurou em: {normais_csv}")

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

    # -------------------------
    # UTILITÁRIOS DE TIMING
    # -------------------------
    @staticmethod
    def format_time(seconds):
        """Formata tempo em segundos para formato legível HH:MM:SS.ss"""
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{seconds:05.2f}"


    # ------------------------
    # FUNÇÃO RODAR VALIDAÇÃO 
    # ------------------------
    def rodar_validacao(self, parquet_file, OUTPUT_DIR, n_rows=None, station=None, csv_path=None):
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"INICIANDO VALIDAÇÃO PARA ESTAÇÃO: {station}")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        con = duckdb.connect(database=":memory:")
        try:
            # Optimize DuckDB settings for better performance and memory management
            con.execute("PRAGMA max_temp_directory_size='100GB'")
            con.execute("SET preserve_insertion_order=false")
            con.execute("SET memory_limit='16GB'")  # Reduced to prevent memory issues
            con.execute("SET threads=4")  # Reduced threads to prevent memory pressure
            con.execute("SET enable_progress_bar=false")  # Disable progress bar for cleaner output
            con.execute("SET enable_object_cache=true")  # Enable object caching
            con.execute("SET enable_http_metadata_cache=true")  # Enable metadata caching
            # Use system temp directory for large operations
            import tempfile
            temp_dir = tempfile.gettempdir()
            con.execute(f"SET temp_directory='{temp_dir}'")

            # Carregar dados com otimizações
            step_start = time.time()
            print("📊 Carregando dados...")
            auxFunctions.carregar_dados(con, parquet_file, n_rows=n_rows,  sample=False, station=station)
            # Optimized acronym cleaning using a single query
            con.execute("UPDATE solar_raw SET acronym = UPPER(TRIM(acronym))")
            print(f"✅ Dados carregados em {time.time() - step_start:.2f} segundos")

            # Preprocessamento e metadados otimizado
            step_start = time.time()
            print("🔧 Preprocessando dados e carregando metadados...")
            df_conversion = auxFunctions.preprocess_conversion_data_fill_time(con, 'solar_raw', "base_fill")
            
            # Load metadata in parallel (these are small datasets)
            df_meta = auxFunctions.load_station_metadata()
            df_normais = auxFunctions.load_normais_climaticas()
            
            # Register tables with optimized settings
            con.register("stations", df_meta)
            con.register("normais_climaticas", df_normais)
            print(f"✅ Preprocessamento concluído em {time.time() - step_start:.2f} segundos")

            # Create optimized solar_with_meta table with better join strategy
            con.execute("""
            CREATE OR REPLACE TABLE solar_with_meta AS
            SELECT 
                s.*, 
                m.latitude, m.longitude,
                n.tp_min, n.tp_max, n.press_min, n.press_max, n.rain_max
            FROM base_fill s
            LEFT JOIN stations m
                ON s.acronym = m.station
            LEFT JOIN normais_climaticas n
                ON s.acronym = n.acronym
            ORDER BY s.acronym, s.timestamp
            """)
            
            # Create indexes for better performance during validation
            try:
                con.execute("CREATE INDEX IF NOT EXISTS idx_solar_meta_acronym ON solar_with_meta(acronym)")
                con.execute("CREATE INDEX IF NOT EXISTS idx_solar_meta_timestamp ON solar_with_meta(timestamp)")
            except:
                pass  # Indexes might not be supported in all DuckDB versions
            

            # Inicializa os validators
            step_start = time.time()
            print("🚀 Inicializando validadores...")
            solar_validator = SolarimetricValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_solar")
            meteo_validator = MeteoValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_meteo")
            print(f"✅ Validadores inicializados em {time.time() - step_start:.2f} segundos")

            # Calcular mu0, azs
            step_start = time.time()
            print("☀️ Calculando ângulos solares (mu0, azs)...")
            try:
                solar_validator.add_mu0_to_duckdb(con=con, table_name="solar_with_meta")
                print(f"✅ Ângulos solares calculados em {time.time() - step_start:.2f} segundos")
            except Exception as e:
                print(f"❌ Erro ao calcular ângulos solares: {e}")
                print("💡 Tentando com configurações de memória reduzidas...")
                # Try with reduced memory settings
                con.execute("SET memory_limit='8GB'")
                con.execute("SET threads=2")
                solar_validator.add_mu0_to_duckdb(con=con, table_name="solar_with_meta")
                print(f"✅ Ângulos solares calculados em {time.time() - step_start:.2f} segundos")
        
            # Calcular Sa e Sum
            step_start = time.time()
            print("📐 Calculando radiação extraterrestre (Sa, Sum)...")
            solar_validator.add_sa_sum(con, table_name="solar_with_meta")
            print(f"✅ Radiação extraterrestre calculada em {time.time() - step_start:.2f} segundos")
            
            # Rodar validação solar se existirem as colunas correspondentes
            colunas_solar = ["glo_avg", "dir_avg", "dif_avg", "lw_avg", "par_avg", "lux_avg"]
            colunas_existentes = con.execute("PRAGMA table_info('solar_with_meta')").fetch_df()['name'].tolist()
            solar_ran = any(col in colunas_existentes for col in colunas_solar)
            
            if solar_ran:
                step_start = time.time()
                print("🔍 Executando validação solarimétrica...")
                solar_validator.run_solar_validation()
                print(f"✅ Validação solarimétrica concluída em {time.time() - step_start:.2f} segundos")
            else:
                print("⚠️ Nenhuma coluna solar encontrada, pulando validação solarimétrica")

            # Rodar validação meteo sem precisar checar colunas
            step_start = time.time()
            print("🌡️ Executando validação meteorológica...")
            meteo_validator.run_all()
            print(f"✅ Validação meteorológica concluída em {time.time() - step_start:.2f} segundos")

            # Criar tabela consolidada final
            step_start = time.time()
            print("📋 Consolidando resultados finais...")
            tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
            
            if solar_ran and "solar_validated_solar" in tables:
                if "solar_validated_meteo" in tables:
                    # Ambas as validações foram executadas
                    try:
                        con.execute("""
                        CREATE OR REPLACE TABLE final_consolidated AS
                        SELECT 
                            COALESCE(s.acronym, m.acronym) as acronym,
                            COALESCE(s.timestamp, m.timestamp) as timestamp,
                            s.year, s.day, s.min,
                            s.glo_avg, s.glo_avg_dqc,
                            s.dir_avg, s.dir_avg_dqc,
                            s.dif_avg, s.dif_avg_dqc,
                            s.lw_avg, s.lw_avg_dqc,
                            s.par_avg, s.par_avg_dqc,
                            s.lux_avg, s.lux_avg_dqc,
                            m.temp_avg, m.temp_avg_dqc,
                            m.temp_max, m.temp_max_dqc,
                            m.temp_min, m.temp_min_dqc,
                            m.rh_avg, m.rh_avg_dqc,
                            m.press_avg, m.press_avg_dqc,
                            m.ws_avg, m.ws_avg_dqc,
                            m.wd_avg, m.wd_avg_dqc,
                            m.rain, m.rain_dqc
                        FROM solar_validated_solar s
                        FULL OUTER JOIN solar_validated_meteo m 
                            ON s.acronym = m.acronym AND s.timestamp = m.timestamp
                        """)
                        print("Final consolidated table created (solar + meteo)")
                    except Exception as e:
                        print(f"❌ Error creating consolidated table (solar + meteo): {e}")
                else:
                    # Apenas solar
                    try:
                        # Create a dynamic SELECT statement based on available columns
                        cols = con.execute("PRAGMA table_info('solar_validated_solar')").fetch_df()['name'].tolist()
                        select_columns = []
                        for col in cols:
                            if col not in ['acronym', 'timestamp', 'year', 'day', 'min']:
                                select_columns.append(col)
                        
                        select_clause = ', '.join(['acronym', 'timestamp', 'year', 'day', 'min'] + select_columns)
                        
                        sql = f"""
                        CREATE OR REPLACE TABLE final_consolidated AS
                        SELECT {select_clause}
                        FROM solar_validated_solar
                        """
                        
                        con.execute(sql)
                        print("Final consolidated table created (solar only)")
                    except Exception as e:
                        print(f"❌ Error creating consolidated table (solar only): {e}")
                        import traceback
                        traceback.print_exc()
            elif "solar_validated_meteo" in tables:
                # Apenas meteo
                try:
                    con.execute("""
                    CREATE OR REPLACE TABLE final_consolidated AS
                    SELECT 
                        acronym, timestamp, year, day, min,
                        temp_avg, temp_avg_dqc,
                        temp_max, temp_max_dqc,
                        temp_min, temp_min_dqc,
                        rh_avg, rh_avg_dqc,
                        press_avg, press_avg_dqc,
                        ws_avg, ws_avg_dqc,
                        wd_avg, wd_avg_dqc,
                        rain, rain_dqc
                    FROM solar_validated_meteo
                    """)
                    print("Final consolidated table created (meteo only)")
                except Exception as e:
                    print(f"❌ Error creating consolidated table (meteo only): {e}")
            else:
                print("Warning: No validation tables were created")

            # Verify final_consolidated table was created
            tables_after = [t[0] for t in con.execute("SHOW TABLES").fetchall()]

            # Salvar por mês usando DuckDB COPY
            if station and "final_consolidated" in tables_after:
                step_start = time.time()
                print("💾 Salvando arquivos CSV...")
                
                # Optimized: Get months and save files in a single query per month
                months_result = con.execute("""
                    SELECT DISTINCT 
                        EXTRACT(YEAR FROM timestamp) as year,
                        EXTRACT(MONTH FROM timestamp) as month
                    FROM final_consolidated 
                    ORDER BY year, month
                """).fetchall()
                
                files_saved = 0
                for year, month in months_result:
                    month_str = f"{int(year):04d}-{int(month):02d}"
                    csv_path = os.path.join(OUTPUT_DIR, station, f"solar_validated_{station}_{month_str}.csv")
                    
                    try:
                        # Optimized: Use prepared statement and better filtering
                        con.execute(f"""
                        COPY (
                            SELECT * FROM final_consolidated 
                            WHERE EXTRACT(YEAR FROM timestamp) = {int(year)}
                            AND EXTRACT(MONTH FROM timestamp) = {int(month)}
                            ORDER BY timestamp
                        ) TO '{csv_path}' (FORMAT CSV, HEADER, DELIMITER ';')
                        """)
                        print(f"📄 Arquivo salvo: {csv_path}")
                        files_saved += 1
                    except Exception as e:
                        print(f"❌ Erro ao salvar {csv_path}: {e}")
                
                print(f"✅ {files_saved} arquivo(s) salvo(s) em {time.time() - step_start:.2f} segundos")
            elif station:
                print("⚠️ Warning: No consolidated table available for file export")

            return None  # Não retornamos mais DataFrame

        finally:
            # Clean up memory and resources
            try:
                # Force garbage collection
                gc.collect()
                # Close connection properly
                con.close()
            except:
                pass  # Ignore errors during cleanup
            
            # Final timing summary
            total_time = time.time() - start_time
            
            print(f"\n{'='*60}")
            print(f"✅ VALIDAÇÃO CONCLUÍDA PARA ESTAÇÃO: {station}")
            print(f"⏱️ TEMPO TOTAL: {self.format_time(total_time)}")
            print(f"📊 TEMPO TOTAL EM SEGUNDOS: {total_time:.2f}s")
            print(f"🕐 FINALIZADO EM: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}\n")   