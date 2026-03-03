import json
path="/home/ubuntu/Zhuolun_project/MNS_Tuutorial/ALPHA-MNS-main/material-screen-calc/ai4m_tqm/src/material_pipeline/pcb_material_kb.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
    print(data)
