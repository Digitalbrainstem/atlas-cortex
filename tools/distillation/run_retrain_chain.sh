#!/bin/bash
# Auto-chain: waits for 4B retrain, then starts 9B retrain
LOG=/workspace/atlas-distillation/retrain_chain.log

echo "Tue Mar 17 17:06:29 UTC 2026 Chain started, waiting for 4B retrain..." >> 

# Wait for 4B retrain to finish
while pgrep -f "retrain_all_loras.py 4b" > /dev/null 2>&1; do
    sleep 60
done

echo "Tue Mar 17 17:06:29 UTC 2026 4B retrain done, backing up..." >> 

# Backup 4B v2 LoRAs
tar czf /workspace/atlas-distillation/backups/phase3v2_loras_core4b.tar.gz     -C /workspace/atlas-distillation/models loras-core-4b-v2/ 2>/dev/null
echo "Tue Mar 17 17:06:29 UTC 2026 4B backup done" >> 

# Start 9B retrain
echo "Tue Mar 17 17:06:29 UTC 2026 Starting 9B retrain..." >> 
python3 /workspace/atlas-distillation/retrain_all_loras.py 9b >> /workspace/atlas-distillation/retrain_9b_log.txt 2>&1

echo "Tue Mar 17 17:06:29 UTC 2026 9B retrain done, backing up..." >> 

# Backup 9B v2 LoRAs
tar czf /workspace/atlas-distillation/backups/phase5v2_loras_ultra9b.tar.gz     -C /workspace/atlas-distillation/models loras-ultra-9b-v2/ 2>/dev/null
echo "Tue Mar 17 17:06:29 UTC 2026 ALL RETRAINING COMPLETE" >> 
