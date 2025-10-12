
import os
import time
import pandas as pd
from datetime import datetime
from core.sondaUtils import auxFunctions

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
PARQUET_FILE = "data/raw/Solarimetrica-001.parquet"

# Pasta de saída
OUTPUT_DIR = "data/output"

# Linhas do 
N_ROWS = None

my_aux = auxFunctions()

# Lista de estações a processar - optimized loading
print("📋 Carregando lista de estações...")
stations = pd.read_parquet(PARQUET_FILE, columns=["acronym"])["acronym"].dropna().unique().tolist()
print(f"✅ {len(stations)} estações encontradas")

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

# Loop principal: uma estação por vez - optimized
station_times = []
for i, station in enumerate(stations, 1):
    station_start = time.time()
    print(f"\n🔄 PROCESSANDO ESTAÇÃO {i}/{len(stations)}: {station}")
    
    # Criar pasta da estação (optimized)
    station_dir = os.path.join(OUTPUT_DIR, station)
    os.makedirs(station_dir, exist_ok=True)

    # Executa validação para a estação
    try:
        my_aux.rodar_validacao(PARQUET_FILE, OUTPUT_DIR, n_rows=N_ROWS, station=station)
        station_time = time.time() - station_start
        station_times.append(station_time)
        print(f"✅ Estação {station} processada em {station_time:.2f} segundos")
    except Exception as e:
        print(f"❌ Erro ao processar estação {station}: {e}")
        station_times.append(0)  # Add 0 time for failed stations
        continue

# Resumo final da execução
script_total_time = time.time() - script_start_time
avg_station_time = sum(station_times) / len(station_times) if station_times else 0

print(f"\n{'='*80}")
print(f"🎉 VALIDAÇÃO COMPLETA FINALIZADA!")
print(f"📊 ESTATÍSTICAS FINAIS:")
print(f"   • Total de estações processadas: {len(stations)}")
print(f"   • Tempo total de execução: {my_aux.format_time(script_total_time)}")
print(f"   • Tempo médio por estação: {avg_station_time:.2f} segundos")
print(f"   • Tempo total em segundos: {script_total_time:.2f}s")
print(f"   • Estação mais rápida: {min(station_times):.2f}s" if station_times else "   • Estação mais rápida: N/A")
print(f"   • Estação mais lenta: {max(station_times):.2f}s" if station_times else "   • Estação mais lenta: N/A")
print(f"🕐 Finalizado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*80}")


def main():
    """Main entry point for the sonda-validation command"""
    # This function can be used as an entry point for the package
    # The actual execution logic is already in the script above
    pass


if __name__ == "__main__":
    # The script runs automatically when executed directly
    pass
 