

# Various DEV tips

## Nautilus :dev copy

```bash
docker rm scrubexif-nautilus-dev-pin 2>/dev/null
docker rmi scrubexif:nautilus-dev    2> /dev/null

docker tag scrubexif:dev scrubexif:nautilus-dev
docker create \
  --name scrubexif-nautilus-dev-pin \
  scrubexif:nautilus-dev >/dev/null
```
