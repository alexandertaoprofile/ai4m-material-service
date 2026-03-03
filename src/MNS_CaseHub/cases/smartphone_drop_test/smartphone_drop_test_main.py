# smartphone_drop_test_main.py

import os
import sys

# 确保能 import 到原始封装代码
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(BASE_DIR, "Smartphone_DropImpact_PINN")
sys.path.insert(0, CASE_DIR)

def main():
    from Smartphone_DropImpact_PINN_main import main as drop_main
    drop_main()

if __name__ == "__main__":
    main()
