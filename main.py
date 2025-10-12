
import os
import pandas as pd
import duckdb
import gc

from core.sondaUtils import auxFunctions
from core.sondaValidator import SolarimetricValidator, MeteoValidator
    
    
# -------------------------
# FUNÇÃO RODAR VALIDAÇÃO
# -------------------------
def rodar_validacao(parquet_file, n_rows=None, station=None, csv_path=None):
    con = duckdb.connect(database=":memory:")
    try:
        # Optimize memory usage
        con.execute("PRAGMA max_temp_directory_size='100GB'")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET memory_limit='32GB'")
        con.execute("SET threads=4")

        # Carregar dados
        auxFunctions.carregar_dados(con, parquet_file, n_rows=n_rows,  sample=False, station=station)
        con.execute("UPDATE solar_raw SET acronym = UPPER(TRIM(acronym))")

        # Preprocessamento e metadados
        df_conversion = auxFunctions.preprocess_conversion_data_fill_time(con, 'solar_raw', "base_fill")
        df_meta = auxFunctions.load_station_metadata()
        df_normais = auxFunctions.load_normais_climaticas()
        con.register("stations", df_meta)
        con.register("normais_climaticas", df_normais)

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
        """)
        

        # Inicializa os validators
        solar_validator = SolarimetricValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_solar")
        meteo_validator = MeteoValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_meteo")

        # Calcular mu0, azs
        solar_validator.add_mu0_to_duckdb(con=con, table_name="solar_with_meta")
    
        # Calcular Sa e Sum
        solar_validator.add_sa_sum(con, table_name="solar_with_meta")
        
        # Rodar validação solar se existirem as colunas correspondentes
        colunas_solar = ["glo_avg", "dir_avg", "dif_avg", "lw_avg", "par_avg", "lux_avg"]
        colunas_existentes = con.execute("PRAGMA table_info('solar_with_meta')").fetch_df()['name'].tolist()
        solar_ran = any(col in colunas_existentes for col in colunas_solar)
        
        if solar_ran:
            solar_validator.run_solar_validation()
            print("Solar validation completed")
        else:
            print("No solar columns found, skipping solar validation")

        # Rodar validação meteo sem precisar checar colunas
        meteo_validator.run_all()
        print("Meteo validation completed")

        # Criar tabela consolidada final
        tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        
        if solar_ran and "solar_validated_solar" in tables:
            if "solar_validated_meteo" in tables:
                # Ambas as validações foram executadas
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
            else:
                # Apenas solar
                con.execute("""
                CREATE OR REPLACE TABLE final_consolidated AS
                SELECT 
                    acronym, timestamp, year, day, min,
                    glo_avg, glo_avg_dqc,
                    dir_avg, dir_avg_dqc,
                    dif_avg, dif_avg_dqc,
                    lw_avg, lw_avg_dqc,
                    par_avg, par_avg_dqc,
                    lux_avg, lux_avg_dqc
                FROM solar_validated_solar
                """)
                print("Final consolidated table created (solar only)")
        elif "solar_validated_meteo" in tables:
            # Apenas meteo
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
        else:
            print("Warning: No validation tables were created")

        # Salvar por dia usando DuckDB COPY
        if station and "final_consolidated" in tables:
            # Obter lista de dias únicos
            days_result = con.execute("SELECT DISTINCT CAST(timestamp AS DATE) as day FROM final_consolidated ORDER BY day").fetchall()
            
            for day, in days_result:
                day_str = day.strftime("%Y-%m-%d")
                csv_path = os.path.join(OUTPUT_DIR, station, f"solar_validated_{station}_{day_str}.csv")
                
                try:
                    con.execute(f"""
                    COPY (
                        SELECT * FROM final_consolidated 
                        WHERE CAST(timestamp AS DATE) = DATE '{day}'
                    ) TO '{csv_path}' (FORMAT CSV, HEADER, DELIMITER ';')
                    """)
                    print(f"Arquivo salvo: {csv_path}")
                except Exception as e:
                    print(f"Erro ao salvar {csv_path}: {e}")
        elif station:
            print("Warning: No consolidated table available for file export")

        return None  # Não retornamos mais DataFrame

    finally:
        # Clean up memory        
        gc.collect()
        con.close()
        print("Conexão DuckDB fechada")





# def rodar_validacao(parquet_file, n_rows=None, station=None, csv_path=None):
#     con = duckdb.connect(database=":memory:")
#     try:
#         con.execute("PRAGMA max_temp_directory_size='50GB'")
#         con.execute("SET preserve_insertion_order=false")

#         # Carregar dados
#         carregar_dados(con, parquet_file, n_rows=n_rows, station=station)
#         con.execute("UPDATE solar_raw SET acronym = UPPER(TRIM(acronym))")

#         # Preprocessamento e metadados
#         df_conversion = preprocess_conversion_data_fill_time(con, 'solar_raw', "base_fill")
#         df_meta = load_station_metadata()
#         df_normais = load_normais_climaticas()
#         con.register("stations", df_meta)
#         con.register("normais_climaticas", df_normais)

#         con.execute("""
#            CREATE OR REPLACE TABLE solar_with_meta AS
#            SELECT 
#                s.*, 
#                m.latitude, m.longitude,
#                n.tp_min, n.tp_max, n.press_min, n.press_max, n.rain_max
#            FROM base_fill s
#            LEFT JOIN stations m
#                ON s.acronym = m.station_normalized
#            LEFT JOIN normais_climaticas n
#                ON s.acronym = n.acronym
#         """)
        
#          # Calcular mu0, azs
#         add_mu0_to_duckdb(con, table_name="solar_with_meta")
    
#         # Calcular Sa e Sum
#         add_sa_sum(con, table_name="solar_with_meta")

#         # Inicializa os validators
#         solar_validator = SolarimetricValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_solar")
#         meteo_validator = MeteoValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_meteo")
        
#         # Rodar validação solar se existirem as colunas correspondentes
#         colunas_solar = ["glo_avg", "dir_avg", "dif_avg", "lw_avg", "par_avg", "lux_avg"]
#         colunas_existentes = con.execute("PRAGMA table_info('solar_with_meta')").fetch_df()['name'].tolist()
#         df_solar = solar_validator.run_solar_validation() if any(col in colunas_existentes for col in colunas_solar) else pd.DataFrame()

#         # Rodar validação meteo sem precisar checar colunas
#         df_meteo = meteo_validator.run_all()

#         # Combinar os dois DataFrames em um só
#         if not df_solar.empty and not df_meteo.empty:
#             df_final = pd.merge(df_solar, df_meteo, on=["acronym", "ts_key"], how="outer")
#         elif not df_solar.empty:
#             df_final = df_solar
#         elif not df_meteo.empty:
#             df_final = df_meteo
#         else:
#             df_final = pd.DataFrame()

#         # Salvar CSV se solicitado
#         if csv_path and not df_final.empty:
#             df_final.to_csv(csv_path, index=False, sep=";")
#             print(f"Arquivo CSV salvo em: {csv_path}")

#         return df_final

#     finally:
#         con.close()
#         print("Conexão DuckDB fechada")
           
            
# -------------------------
# RODADA
# -------------------------

# PARQUET_FILE = "/Users/cacossetin/Documents/Work/Daniel/INPE/validacao/sonda-curadoria-main/Solarimetrica_set_25.parquet"
# CSV_FILE = "/Users/cacossetin/Documents/Work/Daniel/INPE/validacao/sonda-curadoria-main/solar_validated_SBR.csv"

# df_val = rodar_validacao(PARQUET_FILE, n_rows=200, station="SBR", csv_path=CSV_FILE)

###############################
# Caminho do arquivo parquet com todas as estações
PARQUET_FILE = "/home/daniel/inpe/SolterData/INPE_SONDA/sonda_validation/data/raw/Solarimetrica-001.parquet"

# Pasta de saída
OUTPUT_DIR = "/home/daniel/inpe/SolterData/INPE_SONDA/sonda_validation/data/output"

# Linhas do 
N_ROWS = None

# Lista de estações a processar
stations = pd.read_parquet(PARQUET_FILE, columns=["acronym"])["acronym"].dropna().unique().tolist()

# Criar pasta de saída (se não existir)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Loop principal: uma estação por vez
for station in stations:
    print(f"\n=== Rodando validação para {station} ===")

    # Criar pasta da estação
    station_dir = os.path.join(OUTPUT_DIR, station)
    os.makedirs(station_dir, exist_ok=True)

    # Executa validação para a estação
    rodar_validacao(PARQUET_FILE, n_rows=N_ROWS, station=station)
    
    print(f"Validação concluída para {station}")
 