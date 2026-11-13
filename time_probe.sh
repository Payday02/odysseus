start=$(date +%s)
curl -s -X POST http://host.docker.internal:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"qwen-mtp\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}" \
  -o /dev/null
end=$(date +%s)
echo "elapsed: $((end-start))s"