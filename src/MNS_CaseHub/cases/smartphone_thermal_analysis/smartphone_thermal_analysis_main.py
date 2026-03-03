# smartphone_thermal_analysis_main.py

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(BASE_DIR, "Phone2D_HeatPINN")
sys.path.insert(0, CASE_DIR)

def main():
    from Smartphone_Thermal_PINN_main import main as thermal_main
    thermal_main()

if __name__ == "__main__":
    main()
