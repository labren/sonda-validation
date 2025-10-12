
import os
import pandas as pd
import duckdb
import gc
import time
from datetime import datetime, timedelta

from core.sondaUtils import auxFunctions
from core.sondaValidator import SolarimetricValidator, MeteoValidator


# -------------------------
# UTILITÁRIOS DE TIMING
# -------------------------
def format_time(seconds):
    """Formata tempo em segundos para formato legível HH:MM:SS.ss"""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:05.2f}"


# -------------------------
# FUNÇÃO RODAR VALIDAÇÃO
# -------------------------
def rodar_validacao(parquet_file, n_rows=None, station=None, csv_path=None):
    start_time = time.time()
    print(f"\n{'='*60}")
    print(f"INICIANDO VALIDAÇÃO PARA ESTAÇÃO: {station}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    con = duckdb.connect(database=":memory:")
    try:
        # Optimize memory usage
        con.execute("PRAGMA max_temp_directory_size='100GB'")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET memory_limit='32GB'")
        con.execute("SET threads=4")

        # Carregar dados
        step_start = time.time()
        print("📊 Carregando dados...")
        auxFunctions.carregar_dados(con, parquet_file, n_rows=n_rows,  sample=False, station=station)
        con.execute("UPDATE solar_raw SET acronym = UPPER(TRIM(acronym))")
        print(f"✅ Dados carregados em {time.time() - step_start:.2f} segundos")

        # Preprocessamento e metadados
        step_start = time.time()
        print("🔧 Preprocessando dados e carregando metadados...")
        df_conversion = auxFunctions.preprocess_conversion_data_fill_time(con, 'solar_raw', "base_fill")
        df_meta = auxFunctions.load_station_metadata()
        df_normais = auxFunctions.load_normais_climaticas()
        con.register("stations", df_meta)
        con.register("normais_climaticas", df_normais)
        print(f"✅ Preprocessamento concluído em {time.time() - step_start:.2f} segundos")

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
        step_start = time.time()
        print("🚀 Inicializando validadores...")
        solar_validator = SolarimetricValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_solar")
        meteo_validator = MeteoValidator(con, tabela_origem="solar_with_meta", tabela_destino="solar_validated_meteo")
        print(f"✅ Validadores inicializados em {time.time() - step_start:.2f} segundos")

        # Calcular mu0, azs
        step_start = time.time()
        print("☀️ Calculando ângulos solares (mu0, azs)...")
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

        # Salvar por mês usando DuckDB COPY
        if station and "final_consolidated" in tables:
            step_start = time.time()
            print("💾 Salvando arquivos CSV...")
            # Obter lista de meses únicos
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
                    con.execute(f"""
                    COPY (
                        SELECT * FROM final_consolidated 
                        WHERE EXTRACT(YEAR FROM timestamp) = {int(year)}
                        AND EXTRACT(MONTH FROM timestamp) = {int(month)}
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
        # Clean up memory        
        gc.collect()
        con.close()
        
        # Final timing summary
        total_time = time.time() - start_time
        
        print(f"\n{'='*60}")
        print(f"✅ VALIDAÇÃO CONCLUÍDA PARA ESTAÇÃO: {station}")
        print(f"⏱️ TEMPO TOTAL: {format_time(total_time)}")
        print(f"📊 TEMPO TOTAL EM SEGUNDOS: {total_time:.2f}s")
        print(f"🕐 FINALIZADO EM: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")





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

# Início da execução principal
script_start_time = time.time()
print(f"\n{'='*80}")
print(f"🚀 INICIANDO VALIDAÇÃO COMPLETA DO SISTEMA INPE SONDA")
print(f"📅 Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"📊 Total de estações: {len(stations)}")
print(f"📁 Arquivo de entrada: {PARQUET_FILE}")
print(f"📁 Diretório de saída: {OUTPUT_DIR}")
print(f"{'='*80}")

# Loop principal: uma estação por vez
station_times = []
for i, station in enumerate(stations, 1):
    station_start = time.time()
    print(f"\n🔄 PROCESSANDO ESTAÇÃO {i}/{len(stations)}: {station}")
    
    # Criar pasta da estação
    station_dir = os.path.join(OUTPUT_DIR, station)
    os.makedirs(station_dir, exist_ok=True)

    # Executa validação para a estação
    rodar_validacao(PARQUET_FILE, n_rows=N_ROWS, station=station)
    
    station_time = time.time() - station_start
    station_times.append(station_time)
    
    print(f"✅ Estação {station} processada em {station_time:.2f} segundos")

# Resumo final da execução
script_total_time = time.time() - script_start_time
avg_station_time = sum(station_times) / len(station_times) if station_times else 0

print(f"\n{'='*80}")
print(f"🎉 VALIDAÇÃO COMPLETA FINALIZADA!")
print(f"📊 ESTATÍSTICAS FINAIS:")
print(f"   • Total de estações processadas: {len(stations)}")
print(f"   • Tempo total de execução: {format_time(script_total_time)}")
print(f"   • Tempo médio por estação: {avg_station_time:.2f} segundos")
print(f"   • Tempo total em segundos: {script_total_time:.2f}s")
print(f"   • Estação mais rápida: {min(station_times):.2f}s" if station_times else "   • Estação mais rápida: N/A")
print(f"   • Estação mais lenta: {max(station_times):.2f}s" if station_times else "   • Estação mais lenta: N/A")
print(f"🕐 Finalizado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*80}")
 