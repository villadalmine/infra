#!/bin/bash
# Test RK1 NPU Pool inference and status
# Usage: ./scripts/test-npu-pool.sh

set -e

NAMESPACE="ai"
PODS=("rk1-npu-01" "rk1-npu-02" "rk1-npu-03" "rk1-npu-04")

echo -e "\033[1;36m=========================================================\033[0m"
echo -e "\033[1;36m           RK1 NPU Pool E2E Inference Test               \033[0m"
echo -e "\033[1;36m=========================================================\033[0m"
echo ""

for POD_BASE in "${PODS[@]}"; do
    echo -e "\033[1;33m--- Testing ${POD_BASE} ---\033[0m"
    
    # Get actual pod name
    POD_NAME=$(kubectl get pods -n "${NAMESPACE}" -l app="${POD_BASE}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    
    if [ -z "${POD_NAME}" ]; then
        echo -e "\033[1;31m[ERROR] Pod for ${POD_BASE} not found!\033[0m"
        echo ""
        continue
    fi
    
    STATUS=$(kubectl get pod -n "${NAMESPACE}" "${POD_NAME}" -o jsonpath='{.status.phase}')
    echo -e "Pod Name: ${POD_NAME}"
    echo -e "Status:   ${STATUS}"
    
    if [ "${STATUS}" != "Running" ]; then
        echo -e "\033[1;31m[ERROR] Pod is not running!\033[0m"
        echo ""
        continue
    fi
    
    # 1. Check API Tags / loaded models
    echo -e "Checking loaded models..."
    TAGS=$(kubectl exec -n "${NAMESPACE}" "${POD_NAME}" -c rkllm-inference -- curl -s http://localhost:8080/api/tags || true)
    
    if [ -z "${TAGS}" ] || echo "${TAGS}" | grep -q "error"; then
        echo -e "\033[1;31m[FAIL] Could not retrieve tags or error occurred: ${TAGS}\033[0m"
    else
        echo -e "\033[1;32m[OK] Models loaded:\033[0m $(echo "${TAGS}" | jq -r '.models[].name' 2>/dev/null || echo "${TAGS}")"
    fi
    
    # 2. Check Modelfile existence
    echo -e "Checking Modelfile..."
    MODEL_DIR="/opt/rkllama/models/llama-3.1-8b-instruct"
    MODELFILE_EXISTS=$(kubectl exec -n "${NAMESPACE}" "${POD_NAME}" -c rkllm-inference -- test -f "${MODEL_DIR}/Modelfile" && echo "yes" || echo "no")
    
    if [ "${MODELFILE_EXISTS}" = "yes" ]; then
        echo -e "\033[1;32m[OK] Modelfile found in ${MODEL_DIR}\033[0m"
    else
        echo -e "\033[1;31m[WARNING] Modelfile NOT found in ${MODEL_DIR}\033[0m"
    fi
    
    # 3. Perform Inference Test
    echo -e "Sending prompt 'hola'..."
    START_TIME=$(date +%s.%N)
    
    RESPONSE=$(kubectl exec -n "${NAMESPACE}" "${POD_NAME}" -c rkllm-inference -- curl -s http://localhost:8080/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model":"llama-3.1-8b-instruct","messages":[{"role":"user","content":"hola"}],"max_tokens":15}' || true)
    
    END_TIME=$(date +%s.%N)
    DURATION=$(echo "$END_TIME - $START_TIME" | bc 2>/dev/null || echo "0")
    
    if [ -z "${RESPONSE}" ] || echo "${RESPONSE}" | grep -q "error"; then
        echo -e "\033[1;31m[FAIL] Inference failed: ${RESPONSE}\033[0m"
    else
        CONTENT=$(echo "${RESPONSE}" | jq -r '.choices[0].message.content' 2>/dev/null || echo "${RESPONSE}")
        echo -e "\033[1;32m[SUCCESS] Inference completed in ${DURATION}s!\033[0m"
        echo -e "Response: \"${CONTENT}\""
    fi
    echo ""
done

echo -e "\033[1;36m=========================================================\033[0m"
