#!/bin/bash
# Retries ARM instance creation across all 3 Frankfurt ADs until one succeeds.
COMPARTMENT="ocid1.tenancy.oc1..aaaaaaaaes3qt2jju6e74nmhomwapwxswz2vdrhxjwxmjx6kwrxhupt3x3wq"
IMAGE="ocid1.image.oc1.eu-frankfurt-1.aaaaaaaab2msc7qxe4auh5mhnfqx746egseojithkvoe7fnqqzau67u7qhba"
SUBNET="ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaah2c4jqkkipldjwwrxshkua3shqbvu3cvnknhtvjbprjt45qgpp6a"
SSH_KEY="/root/.ssh/oci_instance_key.pub"
OCI="/root/bin/oci"
LOG="/root/projects/ikr/retry_arm.log"
ADS=("hlbH:EU-FRANKFURT-1-AD-1" "hlbH:EU-FRANKFURT-1-AD-2" "hlbH:EU-FRANKFURT-1-AD-3")

echo "[$(date)] Starting ARM retry loop" | tee -a "$LOG"

while true; do
  for AD in "${ADS[@]}"; do
    echo "[$(date)] Trying $AD..." | tee -a "$LOG"
    RESULT=$($OCI compute instance launch \
      --compartment-id "$COMPARTMENT" \
      --availability-domain "$AD" \
      --shape "VM.Standard.A1.Flex" \
      --shape-config '{"ocpus": 4, "memoryInGBs": 24}' \
      --image-id "$IMAGE" \
      --subnet-id "$SUBNET" \
      --assign-public-ip true \
      --display-name "instance-arm-trading" \
      --ssh-authorized-keys-file "$SSH_KEY" \
      --boot-volume-size-in-gbs 50 \
      --query 'data.{"id":"id","lifecycle-state":"lifecycle-state","display-name":"display-name"}' \
      2>&1)

    if echo "$RESULT" | grep -q "lifecycle-state"; then
      echo "[$(date)] SUCCESS! Instance launched in $AD" | tee -a "$LOG"
      echo "$RESULT" | tee -a "$LOG"
      # Extract public IP after instance is running (wait ~90s)
      sleep 90
      INSTANCE_ID=$(echo "$RESULT" | grep -o 'ocid1.instance[^"]*')
      $OCI compute instance list-vnics --instance-id "$INSTANCE_ID" \
        --query 'data[0].{"public-ip":"public-ip"}' 2>&1 | tee -a "$LOG"
      echo "[$(date)] DONE — check $LOG for IP address" | tee -a "$LOG"
      # Remove self from cron
      crontab -l | grep -v "retry_arm.sh" | crontab -
      echo "[$(date)] Cron job removed." | tee -a "$LOG"
      exit 0
    else
      echo "[$(date)] $AD: out of capacity, will retry" | tee -a "$LOG"
    fi
  done

  # Running via cron — exit after one full round, cron will re-invoke
  echo "[$(date)] All ADs full. Exiting (cron will retry in 5 min)." | tee -a "$LOG"
  exit 1
done
