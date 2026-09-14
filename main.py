from fastapi import FastAPI, HTTPException
import pandapower as pp
from pydantic import BaseModel
import os
import uvicorn

app = FastAPI(
    title="Motor de Flujo de Potencia - 20 Buses ACSR 4/0",
    version="4.0.0"
)

class AnalisisParams(BaseModel):
    temperatura_ambiente: float = 30.0  # °C
    factor_proyeccion: float = 1.0      # Factor de crecimiento de demanda

def construir_red_20_buses(temp: float, factor_carga: float):
    net = pp.create_empty_network(f_hz=50.0)
    
    # Parámetros del conductor ACSR 4/0
    r_20 = 0.548           # Ohm/km a 20°C
    alpha = 0.00403        # Coeficiente térmico del aluminio
    r_ajustada = r_20 * (1 + alpha * (temp - 20.0))  # Ajuste térmico
    x_km = 0.410           # Reactancia inductiva Ohm/km
    c_km = 9.5             # Capacitancia nF/km
    max_i_ka = 0.260       # Capacidad máxima admisible (260 A)

    # 1. Creación de 20 barras en 24.9 kV
    buses = [pp.create_bus(net, vn_kv=24.9, name=f"Bus_{i+1}") for i in range(20)]

    # 2. Generador / Subestación Principal (Slack) en la Barra 0
    pp.create_ext_grid(net, bus=buses[0], vm_pu=1.0, mva_base=100.0, name="Subestación Principal ENDE")

    # 3. Topología de líneas ACSR 4/0 (19 tramos principales)
    for i in range(19):
        pp.create_line_from_parameters(
            net, from_bus=buses[i], to_bus=buses[i+1], length_km=3.2,
            r_ohm_per_km=r_ajustada, x_ohm_per_km=x_km, c_nf_per_km=c_km,
            max_i_ka=max_i_ka, name=f"Linea_{i+1}_{i+2}"
        )
    # Cierre de anillo para estabilidad de red
    pp.create_line_from_parameters(
        net, from_bus=buses[19], to_bus=buses[3], length_km=4.5,
        r_ohm_per_km=r_ajustada, x_ohm_per_km=x_km, c_nf_per_km=c_km,
        max_i_ka=max_i_ka, name="Linea_Anillo_Cierre"
    )

    # 4. Cargas distribuidas con factor de proyección
    for i in range(1, 20):
        pp.create_load(net, bus=buses[i], p_mw=0.10 * factor_carga, q_mvar=0.035 * factor_carga, name=f"Carga_{i+1}")

    return net

@app.post("/api/analisis/completo")
def ejecutar_flujo_potencia(params: AnalisisParams):
    net = construir_red_20_buses(params.temperatura_ambiente, params.factor_proyeccion)
    
    # --- FLUJO DE CARGA Y PÉRDIDAS ---
    try:
        pp.runpp(net, algorithm='nr')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fallo de convergencia en Flujo de Carga: {str(e)}")

    perdidas_kw = float(net.res_line["pl_mw"].sum() * 1000)
    min_voltaje_pu = float(net.res_bus["vm_pu"].min())
    max_cargabilidad_pct = float(net.res_line["loading_percent"].max())

    # Extracción de parámetros eléctricos detallados por cada barra (20 barras)
    resultados_buses = []
    for idx in net.bus.index:
        resultados_buses.append({
            "indice_bus": int(idx),
            "nombre_bus": str(net.bus.at[idx, "name"]),
            "voltaje_pu": round(float(net.res_bus.at[idx, "vm_pu"]), 4),
            "angulo_grados": round(float(net.res_bus.at[idx, "va_degree"]), 2),
            "potencia_activa_mw": round(float(net.res_bus.at[idx, "p_mw"]), 4),
            "potencia_reactiva_mvar": round(float(net.res_bus.at[idx, "q_mvar"]), 4)
        })

    r_20 = 0.548
    alpha = 0.00403
    r_efectiva = r_20 * (1 + alpha * (params.temperatura_ambiente - 20.0))

    return {
        "estado": "Flujo de potencia ejecutado con éxito (20 Buses)",
        "datos_operativos_entrada": {
            "temperatura_ambiente_c": params.temperatura_ambiente,
            "resistencia_acsr_40_ohm_km": round(r_efectiva, 4),
            "factor_proyeccion_carga": params.factor_proyeccion
        },
        "resultados_flujo_y_perdidas": {
            "perdidas_tecnicas_totales_kw": round(perdidas_kw, 2),
            "voltaje_minimo_red_pu": round(min_voltaje_pu, 4),
            "maxima_cargabilidad_linea_pct": round(max_cargabilidad_pct, 2)
        },
        "parametros_por_bus": resultados_buses
    }

@app.get("/")
def leer_raiz():
    return {
        "sistema": "Motor de Flujo de Potencia - 20 Buses (ENDE Delbeni)",
        "estado": "Activo",
        "documentacion": "/docs"
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)


