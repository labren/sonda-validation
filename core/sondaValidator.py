from astral import Observer, sun
from timezonefinder import TimezoneFinder
import pandas as pd
import numpy as np


class SolarimetricValidator:
    def __init__(self, con, tabela_origem: str, tabela_destino: str):
        self.con = con
        self.tabela_origem = tabela_origem
        self.tabela_destino = tabela_destino

    def coluna_existe(self, coluna: str) -> bool:
        colunas = {c[1].lower() for c in self.con.execute(f"PRAGMA table_info('{self.tabela_origem}')").fetchall()}
        return coluna.lower() in colunas
    
    @staticmethod
    def calc_mu0_azs(timestamp, lat, lon):
        try:
            if isinstance(timestamp, str):
                timestamp = pd.to_datetime(timestamp)
            obs = Observer(latitude=lat, longitude=lon)
            solar_elev = sun.elevation(obs, timestamp)
            solar_azimuth = sun.azimuth(obs, timestamp)
            theta_z = 90 - solar_elev
            mu0 = np.cos(np.radians(theta_z)) if 0 <= solar_elev <= 90 else 0.0
            return mu0, solar_azimuth
        except Exception as e:
            print(f"Erro em calc_mu0_azs: {e}")
            return None, None


    @staticmethod
    def add_sa_sum(con, table_name="solar_with_meta", S0=1361, UA=1.0):
        sql = f"""
        CREATE OR REPLACE TABLE "{table_name}" AS
        SELECT *,
            {S0} / POWER({UA}, 2) AS Sa,
            COALESCE(dif_avg, 0) + COALESCE(dir_avg, 0) * COALESCE(mu0, 0) AS Sum
        FROM "{table_name}";
        """
        con.execute(sql)
        print(f"Colunas 'Sa' e 'Sum' adicionadas à tabela '{table_name}' com sucesso!")
   

    def add_mu0_to_duckdb(self, con, table_name="solar_with_meta", tz=None):
        # Process in chunks to avoid memory issues - adaptive chunk size
        total_rows = con.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE latitude IS NOT NULL AND longitude IS NOT NULL').fetchone()[0]
        
        # Adaptive chunk size based on dataset size to prevent memory issues
        if total_rows > 1000000:  # Large datasets
            chunk_size = 25000
        elif total_rows > 500000:  # Medium datasets
            chunk_size = 50000
        else:  # Small datasets
            chunk_size = 100000
        
        # First, get a sample to determine timezone
        sample_df = con.execute(f'SELECT latitude, longitude FROM "{table_name}" WHERE latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1').fetchdf()
        
        if sample_df.empty:
            raise ValueError("Sem latitude/longitude após o join. Verifique se 'acronym' casa com 'station' no CSV.")
        
        if tz is None:
            tf = TimezoneFinder()
            first_row = sample_df.iloc[0]
            tz = tf.timezone_at(lat=float(first_row["latitude"]), lng=float(first_row["longitude"]))
            if tz is None:
                raise ValueError("Não foi possível determinar o timezone automaticamente. Forneça 'tz'.")

        # Add mu0 and azs columns with NULL values first
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN mu0 DOUBLE')
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN azs DOUBLE')
        
        # Process in chunks - optimized
        print(f"Processing {total_rows} rows with coordinates in chunks of {chunk_size}")
        
        for offset in range(0, total_rows, chunk_size):
            chunk_num = offset//chunk_size + 1
            total_chunks = (total_rows + chunk_size - 1)//chunk_size
            if chunk_num % 10 == 0 or chunk_num == total_chunks:  # Reduce print frequency
                print(f"Processing chunk {chunk_num}/{total_chunks}")
            
            # Get chunk with optimized query
            chunk_df = con.execute(f'''
                SELECT timestamp, latitude, longitude, rowid 
                FROM "{table_name}" 
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL 
                ORDER BY rowid 
                LIMIT {chunk_size} OFFSET {offset}
            ''').fetchdf()
            
            if chunk_df.empty:
                break
                
            # Convert timezone - optimized
            if chunk_df["timestamp"].dt.tz is None:
                chunk_df["timestamp"] = chunk_df["timestamp"].dt.tz_localize("UTC").dt.tz_convert(tz)
            else:
                chunk_df["timestamp"] = chunk_df["timestamp"].dt.tz_convert(tz)

            # Calculate mu0 and azs - optimized with vectorization
            chunk_df[["mu0", "azs"]] = chunk_df.apply(
                lambda row: pd.Series(self.calc_mu0_azs(row["timestamp"], row["latitude"], row["longitude"])),
                axis=1
            )
            
            # Batch update for better performance - optimized
            update_data = [(row["mu0"], row["azs"], row["rowid"]) for _, row in chunk_df.iterrows()]
            con.executemany(f'''
                    UPDATE "{table_name}" 
                SET mu0 = ?, azs = ? 
                WHERE rowid = ?
            ''', update_data)
            
            # Clear memory after each chunk to prevent accumulation
            del chunk_df, update_data
            import gc
            gc.collect()
        
        print(f"Colunas 'mu0' e 'azs' adicionadas à tabela '{table_name}' com sucesso!")



    # -------------------------
    # FUNÇÕES DE VALIDAÇÃO
    # -------------------------
    def validate_global_h(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_global AS
        SELECT 
            acronym,
            timestamp,
            year,
            day,
            min,
            glo_avg,
            glo_std,
            Sa,
            mu0,
            azs,
            Sum,
            CAST(
                CASE
                    WHEN glo_avg IS NULL OR glo_std IS NULL THEN 5
                    WHEN glo_std = 0 THEN 2
                    WHEN glo_avg < -4 OR glo_avg > (Sa * 1.5 * POWER(mu0, 1.2) + 100) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN glo_avg IS NULL THEN 5
                    WHEN glo_avg < -2 OR glo_avg > (Sa * 1.2 * POWER(mu0, 1.2) + 50) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN glo_avg IS NULL THEN 5
                    WHEN Sum IS NULL OR Sum <= 50 THEN 5
                    WHEN azs < 75 AND ABS(glo_avg / Sum - 1) > 0.10 THEN 2
                    WHEN azs >= 75 AND azs < 93 AND ABS(glo_avg / Sum - 1) > 0.15 THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS glo_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)

    def validate_direta_n(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_dir AS
        SELECT
            acronym,
            timestamp,
            year,
            day,
            min,
            dir_avg,
            dir_std,
            glo_avg,
            mu0,
            Sa,
            CAST(
                CASE
                    WHEN dir_avg IS NULL OR dir_std IS NULL THEN 5
                    WHEN dir_std = 0 THEN 2
                    WHEN dir_avg < -4 OR dir_avg > Sa THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN dir_avg IS NULL THEN 5
                    WHEN dir_avg < -2 OR dir_avg > (Sa * 0.95 * POWER(mu0, 0.2) + 10) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN dir_avg IS NULL THEN 5
                    WHEN (dir_avg * mu0 - 50) > (glo_avg - dir_avg)
                      OR (glo_avg - dir_avg) > (dir_avg * mu0 + 50)
                    THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS dir_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)

    def validate_difusa(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_dif AS
        SELECT
            acronym,
            timestamp,
            year,
            day,
            min,
            dif_avg,
            dif_std,
            glo_avg,
            mu0,
            Sa,
            azs,
            CAST(
                CASE
                    WHEN dif_avg IS NULL OR dif_std IS NULL THEN 5 
                    WHEN dif_std = 0 THEN 2
                    WHEN dif_avg < -4 OR dif_avg > (Sa * 0.95 * POWER(mu0, 1.2) + 50) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN dif_avg IS NULL THEN 5
                    WHEN dif_avg < -2 OR dif_avg > (Sa * 0.75 * POWER(mu0, 1.2) + 30) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN dif_avg IS NULL THEN 5
                    WHEN glo_avg IS NULL OR glo_avg <= 50 THEN 5
                    WHEN azs < 75 AND dif_avg / glo_avg > 1.05 THEN 2
                    WHEN azs >= 75 AND azs < 93 AND dif_avg / glo_avg > 1.10 THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS dif_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)


    def validate_lw(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_lw AS
        SELECT
            acronym,
            timestamp,
            year,
            day,
            min,
            lw_avg,
            lw_std,
            CAST(
                CASE
                    WHEN lw_avg IS NULL OR lw_std IS NULL THEN 5
                    WHEN lw_std = 0 THEN 2
                    WHEN lw_avg < 40 OR lw_avg > 700 THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN lw_avg IS NULL THEN 5
                    WHEN lw_avg < 60 OR lw_avg > 500 THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS lw_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)


    def validate_par(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_par AS
        SELECT
            acronym,
            timestamp,
            year,
            day,
            min,
            par_avg,
            par_std,
            Sa,
            mu0,
            CAST(
                CASE
                    WHEN par_avg IS NULL OR par_std IS NULL THEN 5
                    WHEN par_std = 0 THEN 2
                    WHEN par_avg < -4 OR par_avg > (2.07 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN par_avg IS NULL THEN 5
                    WHEN par_avg < -2 OR par_avg > (2.07 * (Sa * 1.2 * POWER(mu0, 1.2) + 50)) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS par_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)


    def validate_lux(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_lux AS
        SELECT
            acronym,
            timestamp,
            year,
            day,
            min,
            lux_avg,
            lux_std,
            Sa,
            mu0,
            CAST(
                CASE
                    WHEN lux_avg IS NULL OR lux_std IS NULL THEN 5
                    WHEN lux_std = 0 THEN 2
                    WHEN lux_avg < -4 OR lux_avg > (0.1125 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) ||
            CAST(
                CASE
                    WHEN lux_avg IS NULL THEN 5
                    WHEN lux_avg < -2 OR lux_avg > (0.1125 * (Sa * 0.95 * POWER(mu0, 1.2) + 50)) THEN 2
                    ELSE 9
                END AS VARCHAR
            ) AS lux_avg_dqc
        FROM {self.tabela_origem};
        """
        self.con.execute(sql)


    # -------------------------
    # Consolidação das variáveis solarimétricas
    # -------------------------
    def merge_solar_results(self):
        """Consolida os resultados de todas as validações solares em uma única tabela"""
        print("Consolidando resultados solares...")
        
        # Descobrir quais tabelas temporárias existem
        tabelas = [t[0].lower() for t in self.con.execute("SHOW TABLES").fetchall()]

        joins = []
        select_cols = []

        # Mapear nomes de tabelas temporárias para variável e DQC
        mapeamento = {
            "temp_global": ("glo_avg", "glo_avg_dqc"),
            "temp_dir":    ("dir_avg", "dir_avg_dqc"),
            "temp_dif":    ("dif_avg", "dif_avg_dqc"),
            "temp_lw":     ("lw_avg",  "lw_avg_dqc"),
            "temp_par":    ("par_avg", "par_avg_dqc"),
            "temp_lux":    ("lux_avg", "lux_avg_dqc")
            }

        primeira = True
        base_tab = None
        for tab, (col_var, col_dqc) in mapeamento.items():
            if tab in tabelas:
                if primeira:
                    # Primeira tabela vira base
                    select_cols.append(
                        f"g.acronym, g.timestamp, g.year, g.day, g.min, g.{col_var}, g.{col_dqc}"
                    )
                    primeira = False
                    base_tab = tab
                else:
                    select_cols.append(f"{tab}.{col_var}, {tab}.{col_dqc}")
                    joins.append(
                        f"LEFT JOIN {tab} ON g.acronym = {tab}.acronym AND g.timestamp = {tab}.timestamp"
                    )
            else:
                print(f"[AVISO] Pulando {tab} (não existe).")

        if primeira:
            raise RuntimeError("Nenhuma tabela temporária foi criada. Não há resultados para consolidar.")

        # Criar tabela de destino vazia com schema correto
        sql = f"""
        CREATE OR REPLACE TABLE {self.tabela_destino} AS
        SELECT {', '.join(select_cols)}
        FROM {base_tab} g
        {' '.join(joins)}
        WHERE 1=0
        """
        self.con.execute(sql)
        
        # Processar por dia para evitar memory overflow
        days_result = self.con.execute(f"SELECT DISTINCT CAST(timestamp AS DATE) as day FROM {base_tab} ORDER BY day").fetchall()
        total_days = len(days_result)
        
        print(f"Processando {total_days} dias em lotes...")
        
        for i, (day,) in enumerate(days_result, 1):
            if i % 100 == 0 or i == total_days:
                print(f"Processando dia {i}/{total_days}: {day}")
            
            # Inserir dados do dia atual
            sql = f"""
            INSERT INTO {self.tabela_destino}
            SELECT {', '.join(select_cols)}
            FROM {base_tab} g
            {' '.join(joins)}
            WHERE CAST(g.timestamp AS DATE) = DATE '{day}'
            """
            
            self.con.execute(sql)
        
        # Limpar tabelas temporárias
        temp_tables = [tab for tab in mapeamento.keys() if tab in tabelas]
        for table in temp_tables:
            self.con.execute(f"DROP TABLE IF EXISTS {table}")
        
        print(f"Tabela '{self.tabela_destino}' criada com resultados consolidados!")


    # -------------------------
    # Execução das validações solarimétricas
    # -------------------------
    def run_solar_validation(self):
        """Solar validation with reversed DQC order, cascade, and '0' placeholder.

        DQC digit layout (per variable):
          3-alg (glo, dir, dif): pos1=Alg3 | pos2=Alg2 | pos3=Alg1 | pos4='0'
          2-alg (lw, par, lux):  pos1=Alg2 | pos2=Alg1 | pos3='0'

        Cascade: flag=2 at position N forces position N+1 to 2.
                 flag=5 does NOT cascade.
        """
        print("=== INICIANDO VALIDAÇÕES SOLARES OTIMIZADAS ===")

        colunas_existentes = self.con.execute(
            f"PRAGMA table_info('{self.tabela_origem}')"
        ).fetch_df()['name'].tolist()

        cte_cols = []   # raw algorithm columns computed inside the CTE
        sel_cols = []   # final DQC columns with cascade in the outer SELECT

        if "glo_avg" in colunas_existentes:
            cte_cols.append("""
                glo_avg,
                CASE WHEN glo_avg IS NULL OR glo_std IS NULL THEN 5
                     WHEN glo_std = 0 THEN 2
                     WHEN glo_avg < -4 OR glo_avg > (Sa * 1.5 * POWER(mu0, 1.2) + 100) THEN 2
                     ELSE 9 END AS glo_r1,
                CASE WHEN glo_avg IS NULL THEN 5
                     WHEN glo_avg < -2 OR glo_avg > (Sa * 1.2 * POWER(mu0, 1.2) + 50) THEN 2
                     ELSE 9 END AS glo_r2,
                CASE WHEN glo_avg IS NULL THEN 5
                     WHEN Sum IS NULL OR Sum <= 50 THEN 5
                     WHEN azs < 75 AND ABS(glo_avg / Sum - 1) > 0.10 THEN 2
                     WHEN azs >= 75 AND azs < 93 AND ABS(glo_avg / Sum - 1) > 0.15 THEN 2
                     ELSE 9 END AS glo_r3
            """)
            sel_cols.append("""
                glo_avg,
                CAST(glo_r3 AS VARCHAR) ||
                CAST(CASE WHEN glo_r3 = 2 THEN 2 ELSE glo_r2 END AS VARCHAR) ||
                CAST(CASE WHEN glo_r3 = 2 OR glo_r2 = 2 THEN 2 ELSE glo_r1 END AS VARCHAR) AS glo_avg_dqc
            """)

        if "dir_avg" in colunas_existentes:
            cte_cols.append("""
                dir_avg,
                CASE WHEN dir_avg IS NULL OR dir_std IS NULL THEN 5
                     WHEN dir_std = 0 THEN 2
                     WHEN dir_avg < -4 OR dir_avg > Sa THEN 2
                     ELSE 9 END AS dir_r1,
                CASE WHEN dir_avg IS NULL THEN 5
                     WHEN dir_avg < -2 OR dir_avg > (Sa * 0.95 * POWER(mu0, 0.2) + 10) THEN 2
                     ELSE 9 END AS dir_r2,
                CASE WHEN dir_avg IS NULL THEN 5
                     WHEN dir_avg <= 0 THEN 9
                     WHEN (dir_avg * mu0 - 50) > (glo_avg - dir_avg)
                       OR (glo_avg - dir_avg) > (dir_avg * mu0 + 50)
                     THEN 2
                     ELSE 9 END AS dir_r3
            """)
            sel_cols.append("""
                dir_avg,
                CAST(dir_r3 AS VARCHAR) ||
                CAST(CASE WHEN dir_r3 = 2 THEN 2 ELSE dir_r2 END AS VARCHAR) ||
                CAST(CASE WHEN dir_r3 = 2 OR dir_r2 = 2 THEN 2 ELSE dir_r1 END AS VARCHAR) AS dir_avg_dqc
            """)

        if "dif_avg" in colunas_existentes:
            cte_cols.append("""
                dif_avg,
                CASE WHEN dif_avg IS NULL OR dif_std IS NULL THEN 5
                     WHEN dif_std = 0 THEN 2
                     WHEN dif_avg < -4 OR dif_avg > (Sa * 0.95 * POWER(mu0, 1.2) + 50) THEN 2
                     ELSE 9 END AS dif_r1,
                CASE WHEN dif_avg IS NULL THEN 5
                     WHEN dif_avg < -2 OR dif_avg > (Sa * 0.75 * POWER(mu0, 1.2) + 30) THEN 2
                     ELSE 9 END AS dif_r2,
                CASE WHEN dif_avg IS NULL THEN 5
                     WHEN glo_avg IS NULL OR glo_avg <= 50 THEN 5
                     WHEN azs < 75 AND dif_avg / glo_avg > 1.05 THEN 2
                     WHEN azs >= 75 AND azs < 93 AND dif_avg / glo_avg > 1.10 THEN 2
                     ELSE 9 END AS dif_r3
            """)
            sel_cols.append("""
                dif_avg,
                CAST(dif_r3 AS VARCHAR) ||
                CAST(CASE WHEN dif_r3 = 2 THEN 2 ELSE dif_r2 END AS VARCHAR) ||
                CAST(CASE WHEN dif_r3 = 2 OR dif_r2 = 2 THEN 2 ELSE dif_r1 END AS VARCHAR) AS dif_avg_dqc
            """)

        if "lw_avg" in colunas_existentes:
            if "tp_sfc" in colunas_existentes:
                # Raw temp flags — used by lw Alg3 (Stefan-Boltzmann consistency)
                cte_cols.append("""
                    CASE WHEN tp_sfc IS NULL THEN 5
                         WHEN tp_sfc < tp_min OR tp_sfc > tp_max THEN 2
                         ELSE 9 END AS tp_r1_lw,
                    CASE WHEN ABS(tp_sfc - LAG(tp_sfc, 6) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                         WHEN ABS(tp_sfc - LAG(tp_sfc, 6) OVER (PARTITION BY acronym ORDER BY timestamp)) >= 5 THEN 2
                         ELSE 9 END AS tp_r2_lw,
                    CASE WHEN ABS(tp_sfc - LAG(tp_sfc, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                         WHEN ABS(tp_sfc - LAG(tp_sfc, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 0.5 THEN 2
                         ELSE 9 END AS tp_r3_lw
                """)
            cte_cols.append("""
                lw_avg,
                CASE WHEN lw_avg IS NULL OR lw_std IS NULL THEN 5
                     WHEN lw_std = 0 THEN 2
                     WHEN lw_avg < 40 OR lw_avg > 700 THEN 2
                     ELSE 9 END AS lw_r1,
                CASE WHEN lw_avg IS NULL THEN 5
                     WHEN lw_avg < 60 OR lw_avg > 500 THEN 2
                     ELSE 9 END AS lw_r2
            """)
            if "tp_sfc" in colunas_existentes:
                # 3-algorithm: Alg3 (S-B temp consistency) || Alg2 || Alg1
                # Alg3 = 5 when any temp algorithm fires (temp reference unreliable)
                # Alg3 never = 2, so no cascade from Alg3 into Alg2
                sel_cols.append("""
                    lw_avg,
                    CAST(
                        CASE WHEN lw_avg IS NULL THEN 5
                             WHEN tp_r1_lw = 2 OR tp_r2_lw = 2 OR tp_r3_lw = 2 THEN 5
                             ELSE 9 END
                    AS VARCHAR) ||
                    CAST(lw_r2 AS VARCHAR) ||
                    CAST(CASE WHEN lw_r2 = 2 THEN 2 ELSE lw_r1 END AS VARCHAR) AS lw_avg_dqc
                """)
            else:
                # 2-algorithm fallback when tp_sfc not available: leading '0' placeholder
                sel_cols.append("""
                    lw_avg,
                    '0' ||
                    CAST(lw_r2 AS VARCHAR) ||
                    CAST(CASE WHEN lw_r2 = 2 THEN 2 ELSE lw_r1 END AS VARCHAR) AS lw_avg_dqc
                """)

        if "par_avg" in colunas_existentes:
            cte_cols.append("""
                par_avg,
                CASE WHEN par_avg IS NULL OR par_std IS NULL THEN 5
                     WHEN par_std = 0 THEN 2
                     WHEN par_avg < -4 OR par_avg > (2.07 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
                     ELSE 9 END AS par_r1,
                CASE WHEN par_avg IS NULL THEN 5
                     WHEN par_avg < -2 OR par_avg > (2.07 * (Sa * 1.2 * POWER(mu0, 1.2) + 50)) THEN 2
                     ELSE 9 END AS par_r2
            """)
            sel_cols.append("""
                par_avg,
                '0' ||
                CAST(par_r2 AS VARCHAR) ||
                CAST(CASE WHEN par_r2 = 2 THEN 2 ELSE par_r1 END AS VARCHAR) AS par_avg_dqc
            """)

        if "lux_avg" in colunas_existentes:
            cte_cols.append("""
                lux_avg,
                CASE WHEN lux_avg IS NULL OR lux_std IS NULL THEN 5
                     WHEN lux_std = 0 THEN 2
                     WHEN lux_avg < -4 OR lux_avg > (0.1125 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
                     ELSE 9 END AS lux_r1,
                CASE WHEN lux_avg IS NULL THEN 5
                     WHEN lux_avg < -2 OR lux_avg > (0.1125 * (Sa * 0.95 * POWER(mu0, 1.2) + 50)) THEN 2
                     ELSE 9 END AS lux_r2
            """)
            sel_cols.append("""
                lux_avg,
                '0' ||
                CAST(lux_r2 AS VARCHAR) ||
                CAST(CASE WHEN lux_r2 = 2 THEN 2 ELSE lux_r1 END AS VARCHAR) AS lux_avg_dqc
            """)

        if not cte_cols:
            print("Nenhuma coluna solar encontrada para validação")
            return

        sql = f"""
        CREATE OR REPLACE TABLE {self.tabela_destino} AS
        WITH raw AS (
            SELECT
                acronym, timestamp, year, day, min,
                {", ".join(cte_cols)}
            FROM {self.tabela_origem}
        )
        SELECT
            acronym, timestamp, year, day, min,
            {", ".join(sel_cols)}
        FROM raw
        """

        print("Executando validação solarimétrica otimizada...")
        self.con.execute(sql)
        print(f"Validação solarimétrica concluída. Resultados salvos em {self.tabela_destino}")


##SEM VARIAVEIS    
# # -------------------------
# # CLASSE DE VALIDAÇÃO SOLARIMÉTRICA
# # -------------------------
# class SolarimetricValidator:
#     def __init__(self, con, tabela_origem: str, tabela_destino: str):
#         self.con = con
#         self.tabela_origem = tabela_origem
#         self.tabela_destino = tabela_destino
    
#     def coluna_existe(self, coluna: str) -> bool:
#         colunas = {c[1].lower() for c in self.con.execute(f"PRAGMA table_info('{self.tabela_origem}')").fetchall()}
#         return coluna.lower() in colunas

#     # -------------------------
#     # FUNÇÕES DE VALIDAÇÃO
#     # -------------------------
#     def validate_global_h(self):
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_global AS
#         SELECT *,
#             CAST(
#                 CASE
#                     WHEN glo_avg IS NULL OR glo_std IS NULL THEN 5
#                     WHEN glo_std = 0 THEN 2
#                     WHEN glo_avg < -4 OR glo_avg > (Sa * 1.5 * POWER(mu0, 1.2) + 100) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN glo_avg IS NULL THEN 5
#                     WHEN glo_avg < -2 OR glo_avg > (Sa * 1.2 * POWER(mu0, 1.2) + 50) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN glo_avg IS NULL THEN 5
#                     WHEN Sum IS NULL OR Sum <= 50 THEN 5
#                     WHEN azs < 75 AND ABS(glo_avg / Sum - 1) > 0.10 THEN 2
#                     WHEN azs >= 75 AND azs < 93 AND ABS(glo_avg / Sum - 1) > 0.15 THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_glo_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)

#     def validate_direta_n(self):
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_dir AS
#         SELECT *,
#             CAST(
#                 CASE
#                     WHEN dir_avg IS NULL OR dir_std IS NULL THEN 5
#                     WHEN dir_std = 0 THEN 2
#                     WHEN dir_avg < -4 OR dir_avg > Sa THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN dir_avg IS NULL THEN 5
#                     WHEN dir_avg < -2 OR dir_avg > (Sa * 0.95 * POWER(mu0, 0.2) + 10) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN dir_avg IS NULL THEN 5
#                     WHEN (dir_avg * mu0 - 50) > (glo_avg - dir_avg)
#                     OR (glo_avg - dir_avg) > (dir_avg * mu0 + 50)
#                     THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_dir_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)

#     def validate_difusa(self):
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_dif AS
#         SELECT *,
#             CAST(
#                 CASE
#                     WHEN dif_avg IS NULL OR dif_std IS NULL THEN 5 
#                     WHEN dif_std = 0 THEN 2
#                     WHEN dif_avg < -4 OR dif_avg > (Sa * 0.95 * POWER(mu0, 1.2) + 50) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN dif_avg IS NULL THEN 5
#                     WHEN dif_avg < -2 OR dif_avg > (Sa * 0.75 * POWER(mu0, 1.2) + 30) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN dif_avg IS NULL THEN 5
#                     WHEN glo_avg IS NULL OR glo_avg <= 50 THEN 5
#                     WHEN azs < 75 AND dif_avg / glo_avg > 1.05 THEN 2
#                     WHEN azs >= 75 AND azs < 93 AND dif_avg / glo_avg > 1.10 THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_dif_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)

#     def validate_lw(self):     
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_lw AS
#         SELECT *,
#             5.67e-8 AS sigma,
#             CAST(
#                 CASE
#                     WHEN lw_avg IS NULL  OR lw_std IS NULL THEN 5 
#                     WHEN lw_std = 0 THEN 2
#                     WHEN lw_avg < 40 OR lw_avg > 700 THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN lw_avg IS NULL THEN 5
#                     WHEN lw_avg < 60 OR lw_avg > 500 THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_lw_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)

#     def validate_par(self):
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_par AS
#         SELECT *,
#             CAST(
#                 CASE
#                     WHEN par_avg IS NULL OR par_std IS NULL THEN 5
#                     WHEN par_std = 0 THEN 2
#                     WHEN par_avg < -4 OR par_avg > (2.07 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN par_avg IS NULL THEN 5
#                     WHEN par_avg < -2 OR par_avg > (2.07 * (Sa * 1.2 * POWER(mu0, 1.2) + 50)) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_par_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)

#     def validate_lux(self):
#         sql = f"""
#         CREATE OR REPLACE TABLE temp_lux AS
#         SELECT *,
#             CAST(
#                 CASE
#                     WHEN lux_avg IS NULL OR lux_std IS NULL THEN 5
#                     WHEN lux_std = 0 THEN 2
#                     WHEN lux_avg < -4 OR lux_avg > (0.1125 * (Sa * 1.5 * POWER(mu0, 1.2) + 100)) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) ||
#             CAST(
#                 CASE
#                     WHEN lux_avg IS NULL THEN 5
#                     WHEN lux_avg < -2 OR lux_avg > (0.1125 * (Sa * 0.95 * POWER(mu0, 1.2) + 50)) THEN 2
#                     ELSE 9
#                 END AS VARCHAR
#             ) AS DQC_lux_avg
#         FROM {self.tabela_origem};
#         """
#         self.con.execute(sql)
        
        
#     # -------------------------
#     # Consolidação das variáveis solarimétricas
#     # -------------------------
#     def merge_solar_results(self):
#         # Descobrir quais tabelas temporárias existem
#         tabelas = [t[0].lower() for t in self.con.execute("SHOW TABLES").fetchall()]

#         joins = []
#         select_cols = []

#         # Mapear nomes de tabelas temporárias para coluna final
#         mapeamento = {
#             "temp_global": "DQC_glo_avg",
#             "temp_dir":    "DQC_dir_avg",
#             "temp_dif":    "DQC_dif_avg",
#             "temp_lw":     "DQC_lw_avg",
#             "temp_par":    "DQC_par_avg",
#             "temp_lux":    "DQC_lux_avg"}

#         primeira = True
#         for tab, col in mapeamento.items():
#             if tab in tabelas:
#                 if primeira:
#                     select_cols.append(f"g.acronym, g.ts_key, g.{col}")
#                     primeira = False
#                     base_tab = tab
#                 else:
#                     select_cols.append(f"{tab}.{col}")
#                     joins.append(f"LEFT JOIN {tab} ON g.acronym = {tab}.acronym AND g.ts_key = {tab}.ts_key")
#             else:
#                 print(f"[AVISO] Pulando {tab} (não existe).")

#         if primeira:
#             raise RuntimeError("Nenhuma tabela temporária foi criada. Não há resultados para consolidar.")

#         sql = f"""
#             CREATE OR REPLACE TABLE {self.tabela_destino} AS
#             SELECT {', '.join(select_cols)}
#             FROM {base_tab} g
#             {' '.join(joins)}
#         """
#         self.con.execute(sql)
#         return self.con.execute(f"SELECT * FROM {self.tabela_destino}").fetch_df()


#     # -------------------------
#     # Execução das validações solarimétricas
#     # -------------------------
#     def run_solar_validation(self):
#         print("=== INICIANDO VALIDAÇÕES SOLARES ===")
#         validacoes = [
#         ("glo_avg", self.validate_global_h),
#         ("dir_avg", self.validate_direta_n),
#         ("dif_avg", self.validate_difusa),
#         ("lw_avg", self.validate_lw),
#         ("par_avg", self.validate_par),
#         ("lux_avg", self.validate_lux)]

#         tabelas_criadas = []

#         for coluna, func in validacoes:
#             if self.coluna_existe(coluna):
#                 print(f"Validando {coluna}...")
#                 func()
#                 tabelas_criadas.append(f"temp_{coluna}")
#             else:
#                 print(f"[AVISO] Coluna '{coluna}' não encontrada. Pulando validação.")

#         if not tabelas_criadas:
#             raise RuntimeError("Nenhuma tabela temporária foi criada. Não há resultados para consolidar.")

#         print("Consolidando resultados solares...")
#         final_df = self.merge_solar_results()
#         print(f"Validação solar concluída. Resultados salvos em {self.tabela_destino}")
#         return final_df


# -------------------------
# CLASSE COMPLETA DE VALIDAÇÃO METEOROLÓGICA
# -------------------------
class MeteoValidator:
    def __init__(self, con, tabela_origem: str, tabela_destino: str):
        self.con = con
        self.tabela_origem = tabela_origem
        self.tabela_destino = tabela_destino

    def coluna_existe(self, coluna: str) -> bool:
        colunas = {c[1].lower() for c in self.con.execute(f"PRAGMA table_info('{self.tabela_origem}')").fetchall()}
        return coluna.lower() in colunas

    # -------------------------
    # Validações individuais
    # -------------------------
    def validate_tp_sfc(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_tp AS
        WITH base AS (
            SELECT
                m.acronym,
                CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
                CAST(m.tp_sfc AS DOUBLE) AS tp,
                EXTRACT(year FROM m."TIMESTAMP") AS year,
                EXTRACT(day FROM m."TIMESTAMP") AS day,
                EXTRACT(minute FROM m."TIMESTAMP") AS min,
                n.tp_min, n.tp_max
            FROM "{self.tabela_origem}" m
            JOIN normais_climaticas n ON m.acronym = n.acronym
        ),
        win AS (
            SELECT
                b.*,
                ABS(tp - LAG(tp, 6) OVER (PARTITION BY acronym ORDER BY ts)) AS var_1h,
                ABS(tp - LAG(tp, 72) OVER (PARTITION BY acronym ORDER BY ts)) AS var_12h
            FROM base b
        ),
        scored AS (
            SELECT
                w.*,
                CASE WHEN w.tp IS NULL THEN 5
                     WHEN w.tp < w.tp_min OR w.tp > w.tp_max THEN 2 ELSE 9 END AS DQC_alg1,
                CASE WHEN w.var_1h IS NULL THEN 5
                     WHEN w.var_1h >= 5 THEN 2 ELSE 9 END AS DQC_alg2,
                CASE WHEN w.var_12h IS NULL THEN 5
                     WHEN w.var_12h <= 0.5 THEN 2 ELSE 9 END AS DQC_alg3
            FROM win w
        )
        SELECT acronym, ts, year, day, min,
               tp,
               CAST(DQC_alg1 AS VARCHAR) || CAST(DQC_alg2 AS VARCHAR) || CAST(DQC_alg3 AS VARCHAR) AS tp_dqc
        FROM scored;
        """
        self.con.execute(sql)

    def validate_humid(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_humid AS
        SELECT 
            acronym, 
            CAST("TIMESTAMP" AS TIMESTAMP) AS ts,
            EXTRACT(year FROM "TIMESTAMP") AS year,
            EXTRACT(day FROM "TIMESTAMP") AS day,
            EXTRACT(minute FROM "TIMESTAMP") AS min,
            humid,
            CASE WHEN humid >= 0 AND humid <= 100 THEN '9' ELSE '5' END AS humid_dqc
        FROM "{self.tabela_origem}";
        """
        self.con.execute(sql)

    def validate_press(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_press AS
        WITH base AS (
            SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
                   CAST(m.press AS DOUBLE) AS pres,
                   EXTRACT(year FROM m."TIMESTAMP") AS year,
                   EXTRACT(day FROM m."TIMESTAMP") AS day,
                   EXTRACT(minute FROM m."TIMESTAMP") AS min,
                   n.press_min, n.press_max
            FROM "{self.tabela_origem}" m
            JOIN normais_climaticas n ON m.acronym = n.acronym
        ),
        win AS (
            SELECT b.*, ABS(pres - LAG(pres, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h
            FROM base b
        )
        SELECT acronym, ts, year, day, min, pres,
               CAST(
                   CASE WHEN pres IS NULL THEN 5
                        WHEN pres < press_min OR pres > press_max THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN var_3h IS NULL THEN 5
                        WHEN var_3h < 6 THEN 2 ELSE 9 END AS VARCHAR
               ) AS press_dqc
        FROM win;
        """
        self.con.execute(sql)

    def validate_rain(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_rain AS
        WITH base AS (
            SELECT m.acronym,
                   CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
                   CAST(m.rain AS DOUBLE) AS rain,
                   EXTRACT(year FROM m."TIMESTAMP") AS year,
                   EXTRACT(day FROM m."TIMESTAMP") AS day,
                   EXTRACT(minute FROM m."TIMESTAMP") AS min,
                   n.rain_max
            FROM "{self.tabela_origem}" m
            JOIN normais_climaticas n ON m.acronym = n.acronym
        ),
        win AS (
            SELECT b.*,
                   SUM(rain) OVER (
                       PARTITION BY acronym 
                       ORDER BY ts
                       ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
                   ) AS acc_1h,
                   SUM(rain) OVER (
                       PARTITION BY acronym 
                       ORDER BY ts
                       ROWS BETWEEN 143 PRECEDING AND CURRENT ROW
                   ) AS acc_24h
            FROM base b
        )
        SELECT acronym, ts, year, day, min, rain,
               CAST(
                   CASE WHEN rain IS NULL THEN 5
                        WHEN rain < 0 OR rain > rain_max THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN acc_1h IS NULL THEN 5
                        WHEN acc_1h > 25 THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN acc_24h IS NULL THEN 5
                        WHEN acc_24h > 100 THEN 2 ELSE 9 END AS VARCHAR
               ) AS rain_dqc
        FROM win;
        """
        self.con.execute(sql)

    def validate_wind(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_wind AS
        WITH base AS (
            SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
                   CAST(m.ws10_avg AS DOUBLE) AS wind,
                   EXTRACT(year FROM m."TIMESTAMP") AS year,
                   EXTRACT(day FROM m."TIMESTAMP") AS day,
                   EXTRACT(minute FROM m."TIMESTAMP") AS min
            FROM "{self.tabela_origem}" m
        ),
        win AS (
            SELECT b.*,
                   ABS(wind - LAG(wind, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h,
                   ABS(wind - LAG(wind, 72) OVER (PARTITION BY acronym ORDER BY ts)) AS var_12h
            FROM base b
        )
        SELECT acronym, ts, year, day, min, wind,
               CAST(
                   CASE WHEN wind IS NULL THEN 5
                        WHEN wind < 0 OR wind > 25 THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN var_3h IS NULL THEN 5
                        WHEN var_3h <= 0.1 THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN var_12h IS NULL THEN 5
                        WHEN var_12h <= 0.5 THEN 2 ELSE 9 END AS VARCHAR
               ) AS wind_dqc
        FROM win;
        """
        self.con.execute(sql)

    def validate_wind_dir(self):
        sql = f"""
        CREATE OR REPLACE TEMP TABLE temp_wind_dir AS
        WITH base AS (
            SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
                   CAST(m.wd10_avg AS DOUBLE) AS wind_dir,
                   EXTRACT(year FROM m."TIMESTAMP") AS year,
                   EXTRACT(day FROM m."TIMESTAMP") AS day,
                   EXTRACT(minute FROM m."TIMESTAMP") AS min
            FROM "{self.tabela_origem}" m
        ),
        win AS (
            SELECT b.*,
                   ABS(wind_dir - LAG(wind_dir, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h,
                   ABS(wind_dir - LAG(wind_dir, 108) OVER (PARTITION BY acronym ORDER BY ts)) AS var_18h
            FROM base b
        )
        SELECT acronym, ts, year, day, min, wind_dir,
               CAST(
                   CASE WHEN wind_dir IS NULL THEN 5
                        WHEN wind_dir < 0 OR wind_dir > 360 THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN var_3h IS NULL THEN 5
                        WHEN var_3h <= 1 THEN 2 ELSE 9 END AS VARCHAR
               ) ||
               CAST(
                   CASE WHEN var_18h IS NULL THEN 5
                        WHEN var_18h <= 10 THEN 2 ELSE 9 END AS VARCHAR
               ) AS wind_dir_dqc
        FROM win;
        """
        self.con.execute(sql)


    # -------------------------
    # Consolidação dos resultados
    # -------------------------
    def merge_results(self):
        existing_tables = [t[0].lower() for t in self.con.execute("SHOW TABLES").fetchall()]

        mapeamento = {
            "temp_tp":       ("tp",       "tp",       "tp_dqc"),
            "temp_humid":    ("humid",    "humid",    "humid_dqc"),
            "temp_press":    ("press",    "pres",     "press_dqc"),
            "temp_rain":     ("rain",     "rain",     "rain_dqc"),
            "temp_wind":     ("wind",     "wind",     "wind_dqc"),
            "temp_wind_dir": ("wind_dir", "wind_dir", "wind_dir_dqc")
        }

        select_cols = []
        joins = []
        base_tbl = None

        for tbl, (_, val_col, dqc_col) in mapeamento.items():
            if tbl in existing_tables:
                if base_tbl is None:
                    base_tbl = tbl
                    select_cols.extend([
                        f"{tbl}.acronym", f"{tbl}.ts", f"{tbl}.year", f"{tbl}.day", f"{tbl}.min",
                        f"{tbl}.{val_col}", f"{tbl}.{dqc_col}"
                    ])
                    from_clause = tbl
                else:
                    select_cols.extend([f"{tbl}.{val_col}", f"{tbl}.{dqc_col}"])
                    joins.append(f"LEFT JOIN {tbl} ON {base_tbl}.acronym = {tbl}.acronym AND {base_tbl}.ts = {tbl}.ts")
            else:
                print(f"[AVISO] Pulando {tbl} (não existe).")

        if base_tbl is None:
            raise RuntimeError("Nenhuma tabela temporária foi criada. Não há resultados para consolidar.")

        sql = f"""
        CREATE OR REPLACE TABLE {self.tabela_destino} AS
        SELECT {', '.join(select_cols)}
        FROM {from_clause}
        {' '.join(joins)}
        """
        self.con.execute(sql)
        return self.con.execute(f"SELECT * FROM {self.tabela_destino}").fetch_df()

    # -------------------------
    # Execução das validações
    # -------------------------
    def run_all(self):
        """Meteo validation with reversed DQC order, cascade, and '0' placeholder.

        DQC digit layout (per variable):
          3-alg (temp, ws, wd, rain): pos1=Alg3 | pos2=Alg2 | pos3=Alg1 | pos4='0'
          2-alg (press):              pos1=Alg2 | pos2=Alg1 | pos3='0'
          1-alg (humid):              pos1=Alg1 | pos2='0'

        Cascade: flag=2 at position N forces position N+1 to 2.
                 flag=5 does NOT cascade.
        """
        print("=== INICIANDO VALIDAÇÕES METEOROLÓGICAS OTIMIZADAS ===")

        colunas_existentes = self.con.execute(
            f"PRAGMA table_info('{self.tabela_origem}')"
        ).fetch_df()['name'].tolist()

        cte_cols = []   # raw algorithm columns computed inside the CTE
        sel_cols = []   # final DQC columns with cascade in the outer SELECT

        if "tp_sfc" in colunas_existentes:
            cte_cols.append("""
                CAST(tp_sfc AS DOUBLE) AS temp_avg,
                CASE WHEN tp_sfc IS NULL THEN 5
                     WHEN tp_sfc < tp_min OR tp_sfc > tp_max THEN 2
                     ELSE 9 END AS tp_r1,
                CASE WHEN ABS(tp_sfc - LAG(tp_sfc, 6) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(tp_sfc - LAG(tp_sfc, 6) OVER (PARTITION BY acronym ORDER BY timestamp)) >= 5 THEN 2
                     ELSE 9 END AS tp_r2,
                CASE WHEN ABS(tp_sfc - LAG(tp_sfc, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(tp_sfc - LAG(tp_sfc, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 0.5 THEN 2
                     ELSE 9 END AS tp_r3
            """)
            sel_cols.append("""
                temp_avg,
                CAST(tp_r3 AS VARCHAR) ||
                CAST(CASE WHEN tp_r3 = 2 THEN 2 ELSE tp_r2 END AS VARCHAR) ||
                CAST(CASE WHEN tp_r3 = 2 OR tp_r2 = 2 THEN 2 ELSE tp_r1 END AS VARCHAR) AS temp_avg_dqc
            """)

        if "humid" in colunas_existentes:
            cte_cols.append("""
                humid AS rh_avg,
                CASE WHEN humid >= 0 AND humid <= 100 THEN 9 ELSE 5 END AS rh_r1
            """)
            sel_cols.append("""
                rh_avg,
                '00' || CAST(rh_r1 AS VARCHAR) AS rh_avg_dqc
            """)

        if "press" in colunas_existentes:
            cte_cols.append("""
                CAST(press AS DOUBLE) AS press_avg,
                CASE WHEN press IS NULL THEN 5
                     WHEN press < press_min OR press > press_max THEN 2
                     ELSE 9 END AS press_r1,
                CASE WHEN ABS(press - LAG(press, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(press - LAG(press, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) < 6 THEN 2
                     ELSE 9 END AS press_r2
            """)
            sel_cols.append("""
                press_avg,
                '0' ||
                CAST(press_r2 AS VARCHAR) ||
                CAST(CASE WHEN press_r2 = 2 THEN 2 ELSE press_r1 END AS VARCHAR) AS press_avg_dqc
            """)

        if "ws10_avg" in colunas_existentes:
            cte_cols.append("""
                CAST(ws10_avg AS DOUBLE) AS ws_avg,
                CASE WHEN ws10_avg IS NULL THEN 5
                     WHEN ws10_avg < 0 OR ws10_avg > 25 THEN 2
                     ELSE 9 END AS ws_r1,
                CASE WHEN ABS(ws10_avg - LAG(ws10_avg, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(ws10_avg - LAG(ws10_avg, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 0.1 THEN 2
                     ELSE 9 END AS ws_r2,
                CASE WHEN ABS(ws10_avg - LAG(ws10_avg, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(ws10_avg - LAG(ws10_avg, 72) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 0.5 THEN 2
                     ELSE 9 END AS ws_r3
            """)
            sel_cols.append("""
                ws_avg,
                CAST(ws_r3 AS VARCHAR) ||
                CAST(CASE WHEN ws_r3 = 2 THEN 2 ELSE ws_r2 END AS VARCHAR) ||
                CAST(CASE WHEN ws_r3 = 2 OR ws_r2 = 2 THEN 2 ELSE ws_r1 END AS VARCHAR) AS ws_avg_dqc
            """)

        if "wd10_avg" in colunas_existentes:
            cte_cols.append("""
                CAST(wd10_avg AS DOUBLE) AS wd_avg,
                CASE WHEN wd10_avg IS NULL THEN 5
                     WHEN wd10_avg < 0 OR wd10_avg > 360 THEN 2
                     ELSE 9 END AS wd_r1,
                CASE WHEN ABS(wd10_avg - LAG(wd10_avg, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(wd10_avg - LAG(wd10_avg, 18) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 1 THEN 2
                     ELSE 9 END AS wd_r2,
                CASE WHEN ABS(wd10_avg - LAG(wd10_avg, 108) OVER (PARTITION BY acronym ORDER BY timestamp)) IS NULL THEN 5
                     WHEN ABS(wd10_avg - LAG(wd10_avg, 108) OVER (PARTITION BY acronym ORDER BY timestamp)) <= 10 THEN 2
                     ELSE 9 END AS wd_r3
            """)
            sel_cols.append("""
                wd_avg,
                CAST(wd_r3 AS VARCHAR) ||
                CAST(CASE WHEN wd_r3 = 2 THEN 2 ELSE wd_r2 END AS VARCHAR) ||
                CAST(CASE WHEN wd_r3 = 2 OR wd_r2 = 2 THEN 2 ELSE wd_r1 END AS VARCHAR) AS wd_avg_dqc
            """)

        if "rain" in colunas_existentes:
            cte_cols.append("""
                CAST(rain AS DOUBLE) AS rain,
                CASE WHEN rain IS NULL THEN 5
                     WHEN rain < 0 OR rain > rain_max THEN 2
                     ELSE 9 END AS rain_r1,
                CASE WHEN SUM(rain) OVER (PARTITION BY acronym ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) IS NULL THEN 5
                     WHEN SUM(rain) OVER (PARTITION BY acronym ORDER BY timestamp ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) > 25 THEN 2
                     ELSE 9 END AS rain_r2,
                CASE WHEN SUM(rain) OVER (PARTITION BY acronym ORDER BY timestamp ROWS BETWEEN 143 PRECEDING AND CURRENT ROW) IS NULL THEN 5
                     WHEN SUM(rain) OVER (PARTITION BY acronym ORDER BY timestamp ROWS BETWEEN 143 PRECEDING AND CURRENT ROW) > 100 THEN 2
                     ELSE 9 END AS rain_r3
            """)
            sel_cols.append("""
                rain,
                CAST(rain_r3 AS VARCHAR) ||
                CAST(CASE WHEN rain_r3 = 2 THEN 2 ELSE rain_r2 END AS VARCHAR) ||
                CAST(CASE WHEN rain_r3 = 2 OR rain_r2 = 2 THEN 2 ELSE rain_r1 END AS VARCHAR) AS rain_dqc
            """)

        if not cte_cols:
            print("Nenhuma coluna meteorológica encontrada para validação")
            return

        sql = f"""
        CREATE OR REPLACE TABLE {self.tabela_destino} AS
        WITH raw AS (
            SELECT
                acronym,
                timestamp,
                EXTRACT(year FROM timestamp) AS year,
                EXTRACT(day FROM timestamp) AS day,
                EXTRACT(minute FROM timestamp) AS min,
                {", ".join(cte_cols)}
            FROM {self.tabela_origem}
        )
        SELECT
            acronym, timestamp, year, day, min,
            {", ".join(sel_cols)}
        FROM raw
        """

        print("Executando validação meteorológica otimizada...")
        self.con.execute(sql)
        print(f"Validação meteorológica concluída. Resultados salvos em {self.tabela_destino}")
        return pd.DataFrame()


## SEM VARIAVEIS    
# # -------------------------
# # CLASSE DE VALIDAÇÃO METEOROLÓGICA
# # -------------------------
# class MeteoValidator:
#     def __init__(self, con, tabela_origem: str, tabela_destino: str):
#         self.con = con
#         self.tabela_origem = tabela_origem
#         self.tabela_destino = tabela_destino

#     def coluna_existe(self, coluna: str) -> bool:
#         colunas = {c[1].lower() for c in self.con.execute(f"PRAGMA table_info('{self.tabela_origem}')").fetchall()}
#         return coluna.lower() in colunas

#     # -------------------------
#     # Validações individuais
#     # -------------------------
#     def validate_tp_sfc(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_tp AS
#         WITH base AS (
#             SELECT
#                 m.acronym,
#                 CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
#                 CAST(m.tp_sfc AS DOUBLE) AS tp,
#                 n.tp_min, n.tp_max
#             FROM "{self.tabela_origem}" m
#             JOIN normais_climaticas n ON m.acronym = n.acronym
#         ),
#         win AS (
#             SELECT
#                 b.*,
#                 ABS(tp - LAG(tp, 6) OVER (PARTITION BY acronym ORDER BY ts))  AS var_1h,
#                 ABS(tp - LAG(tp, 72) OVER (PARTITION BY acronym ORDER BY ts)) AS var_12h
#             FROM base b
#         ),
#         scored AS (
#             SELECT
#                 w.*,
#                 CASE WHEN w.tp IS NULL THEN 5
#                      WHEN w.tp < w.tp_min OR w.tp > w.tp_max THEN 2 ELSE 9 END AS DQC_alg1,
#                 CASE WHEN w.var_1h IS NULL THEN 5
#                      WHEN w.var_1h >= 5 THEN 2 ELSE 9 END AS DQC_alg2,
#                 CASE WHEN w.var_12h IS NULL THEN 5
#                      WHEN w.var_12h <= 0.5 THEN 2 ELSE 9 END AS DQC_alg3
#             FROM win w
#         )
#         SELECT acronym, ts,
#                CAST(DQC_alg1 AS VARCHAR) || CAST(DQC_alg2 AS VARCHAR) || CAST(DQC_alg3 AS VARCHAR) AS DQC_temp
#         FROM scored;
#         """
#         self.con.execute(sql)

#     def validate_humid(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_humid AS
#         SELECT acronym, CAST("TIMESTAMP" AS TIMESTAMP) ts,
#                CASE WHEN humid >= 0 AND humid <= 100 THEN '9' ELSE '5' END AS DQC_humid
#         FROM "{self.tabela_origem}";
#         """
#         self.con.execute(sql)

#     def validate_press(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_press AS
#         WITH base AS (
#             SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
#                    CAST(m.press AS DOUBLE) AS pres,
#                    n.press_min, n.press_max
#             FROM "{self.tabela_origem}" m
#             JOIN normais_climaticas n ON m.acronym = n.acronym
#         ),
#         win AS (
#             SELECT b.*, ABS(pres - LAG(pres, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h
#             FROM base b
#         )
#         SELECT acronym, ts,
#                CAST(
#                    CASE WHEN pres IS NULL THEN 5
#                         WHEN pres < press_min OR pres > press_max THEN 2 ELSE 9 END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE WHEN var_3h IS NULL THEN 5
#                         WHEN var_3h < 6 THEN 2 ELSE 9 END AS VARCHAR
#                ) AS DQC_press
#         FROM win;
#         """
#         self.con.execute(sql)
    
#     def validate_rain(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_rain AS
#         WITH base AS (
#             SELECT m.acronym,
#                    CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
#                    CAST(m.rain AS DOUBLE) AS rain,
#                    n.rain_max
#             FROM "{self.tabela_origem}" m
#             JOIN normais_climaticas n ON m.acronym = n.acronym
#         ),
#         win AS (
#             SELECT b.*,
#                    SUM(rain) OVER (
#                        PARTITION BY acronym 
#                        ORDER BY ts
#                        ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
#                    ) AS acc_1h,
#                    SUM(rain) OVER (
#                        PARTITION BY acronym 
#                        ORDER BY ts
#                        ROWS BETWEEN 143 PRECEDING AND CURRENT ROW
#                    ) AS acc_24h
#             FROM base b
#         )
#         SELECT acronym, ts,
#                CAST(
#                    CASE 
#                        WHEN rain IS NULL THEN 5
#                        WHEN rain < 0 OR rain > rain_max THEN 2 ELSE 9 
#                    END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE 
#                        WHEN acc_1h IS NULL THEN 5
#                        WHEN acc_1h > 25 THEN 2 ELSE 9 
#                    END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE 
#                        WHEN acc_24h IS NULL THEN 5
#                        WHEN acc_24h > 100 THEN 2 ELSE 9 
#                    END AS VARCHAR
#                ) AS DQC_rain
#         FROM win;
#         """
#         self.con.execute(sql)
    
#     def validate_wind(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_wind AS
#         WITH base AS (
#             SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
#                    CAST(m.ws10_avg AS DOUBLE) AS wind
#             FROM "{self.tabela_origem}" m
#         ),
#         win AS (
#             SELECT b.*,
#                    ABS(wind - LAG(wind, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h,
#                    ABS(wind - LAG(wind, 72) OVER (PARTITION BY acronym ORDER BY ts)) AS var_12h
#             FROM base b
#         )
#         SELECT acronym, ts,
#                CAST(
#                    CASE WHEN wind IS NULL THEN 5
#                         WHEN wind < 0 OR wind > 25 THEN 2 ELSE 9 END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE WHEN var_3h IS NULL THEN 5
#                         WHEN var_3h <= 0.1 THEN 2 ELSE 9 END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE WHEN var_12h IS NULL THEN 5
#                         WHEN var_12h <= 0.5 THEN 2 ELSE 9 END AS VARCHAR
#                ) AS DQC_wind
#         FROM win;
#         """
#         self.con.execute(sql)

#     def validate_wind_dir(self):
#         sql = f"""
#         CREATE OR REPLACE TEMP TABLE temp_wind_dir AS
#         WITH base AS (
#             SELECT m.acronym, CAST(m."TIMESTAMP" AS TIMESTAMP) AS ts,
#                    CAST(m.wd10_avg AS DOUBLE) AS wind_dir
#             FROM "{self.tabela_origem}" m
#         ),
#         win AS (
#             SELECT b.*,
#                    ABS(wind_dir - LAG(wind_dir, 18) OVER (PARTITION BY acronym ORDER BY ts)) AS var_3h,
#                    ABS(wind_dir - LAG(wind_dir, 108) OVER (PARTITION BY acronym ORDER BY ts)) AS var_18h
#             FROM base b
#         )
#         SELECT acronym, ts,
#                CAST(
#                    CASE WHEN wind_dir IS NULL THEN 5
#                         WHEN wind_dir < 0 OR wind_dir > 360 THEN 2 ELSE 9 END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE WHEN var_3h IS NULL THEN 5
#                         WHEN var_3h <= 1 THEN 2 ELSE 9 END AS VARCHAR
#                ) ||
#                CAST(
#                    CASE WHEN var_18h IS NULL THEN 5
#                         WHEN var_18h <= 10 THEN 2 ELSE 9 END AS VARCHAR
#                ) AS DQC_wind_dir
#         FROM win;
#         """
#         self.con.execute(sql)

#     # -------------------------
#     # Consolidação dos resultados
#     # -------------------------
#     def merge_results(self):
#         existing_tables = [t[0].lower() for t in self.con.execute("SHOW TABLES").fetchall()]

#         join_steps = {
#             "temp_tp":       ("tp",  "tp.DQC_temp"),
#             "temp_humid":    ("h",   "h.DQC_humid"),
#             "temp_press":    ("p",   "p.DQC_press"),
#             "temp_rain":     ("r",   "r.DQC_rain"),
#             "temp_wind":     ("w",   "w.DQC_wind"),
#             "temp_wind_dir": ("wd",  "wd.DQC_wind_dir")}

#         select_cols = []
#         join_clauses = []
#         base_alias = None

#         for tbl, (alias, col) in join_steps.items():
#             if tbl in existing_tables:
#                 if base_alias is None:
#                     base_alias = alias
#                     select_cols.extend([f"{alias}.acronym", f"{alias}.ts", col])
#                     from_clause = f"{tbl} {alias}"
#                 else:
#                     select_cols.append(col)
#                     join_clauses.append(
#                         f"LEFT JOIN {tbl} {alias} ON {base_alias}.acronym = {alias}.acronym "
#                         f"AND {base_alias}.ts = {alias}.ts")
#             else:
#                 print(f"[AVISO] Pulando {tbl} (não existe).")

#         if not base_alias:
#             raise RuntimeError("Nenhuma tabela temporária foi criada. Não há resultados para consolidar.")

#         sql = f"""
#             CREATE OR REPLACE TABLE {self.tabela_destino} AS
#             SELECT {', '.join(select_cols)}
#             FROM {from_clause}
#             {' '.join(join_clauses)}
#         """
#         self.con.execute(sql)
#         return self.con.execute(f"SELECT * FROM {self.tabela_destino}").fetch_df()

#     # -------------------------
#     # Execução das validações
#     # -------------------------
#     def run_all(self):
#         print("=== INICIANDO VALIDAÇÕES METEOROLÓGICAS ===")
#         validacoes = [
#             ("tp", self.validate_tp_sfc),
#             ("humid", self.validate_humid),
#             ("press", self.validate_press),
#             ("rain", self.validate_rain),
#             ("wind", self.validate_wind),
#             ("wind_dir", self.validate_wind_dir)]

#         tabelas_criadas = []

#         for coluna, func in validacoes:
#             if self.coluna_existe(coluna):
#                 print(f"Validando {coluna}...")
#                 func()
#                 tabelas_criadas.append(f"temp_{coluna}")
#             else:
#                 print(f"[AVISO] Coluna '{coluna}' não encontrada. Pulando validação.")

#         if not tabelas_criadas:
#             return pd.DataFrame()  # Retorna DF vazio se não houver tabelas

#         print("Consolidando resultados...")
#         final_df = self.merge_results()
#         print(f"Validação concluída. Resultados salvos em {self.tabela_destino}")
#         return final_df