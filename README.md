# shUMEM
Quanto do ganho do desacoplamento sobrevive quando a decisão de compartilhamento de buffers é tomada por software, em granularidade de lote (µs), em vez de pelo ASIC, por pacote (ns), em NICs comerciais não modificadas?

O contexto completo do projeto, as decisões e tudo o que já foi descoberto estão no [`CLAUDE.md`](CLAUDE.md).

## Preparar as máquinas (depois de instanciar o perfil no CloudLab)

O perfil cria dois nós, `dut` (onde roda o l3fwd) e `tgen` (onde roda o TRex), com o repositório em `/local/repository`. A cada boot, o `setup/node-setup.sh` roda sozinho: reserva hugepages e desliga os pause frames da NIC do experimento.

**No `dut`** (~20 min, compila o DPDK do shRing, o DPDK vanilla e o PCM):

```bash
sudo /local/repository/setup/dev-environment.sh dut
```

**No `tgen`**, com o `dut` ligado (o script aprende o MAC dele pelo link):

```bash
sudo /local/repository/setup/dev-environment.sh tgen
sudo /local/repository/setup/trex-setup.sh
```

O `trex-setup.sh` compila a rdma-core v44 em `/usr/local` (o TRex exige), baixa o TRex v3.07 em `/mydata/trex` e gera o `/etc/trex_cfg.yaml` a partir dos PCIs e MACs da instanciação atual.

**Conferência rápida nos dois nós** (esperado: `RX: off` e `TX: off`):

```bash
sudo ethtool -a $(ls /sys/bus/pci/devices/0000:51:00.0/net/)
```

## Rodar o experimento de comparação

Use `tmux` nas janelas que ficam rodando.

1. **`tgen`, janela 1: servidor TRex** (deixe rodando):
   ```bash
   cd /mydata/trex/v3.07 && sudo ./t-rex-64 -i -c 6 --no-ofed-check
   ```
2. **`dut`: l3fwd com o sistema a medir** (`privring-1024`, `privring-128` ou `shring-8`):
   ```bash
   sudo /local/repository/harness/run_l3fwd.sh privring-1024
   ```
   O script aprende o MAC do `tgen` pelo link. Se não conseguir, passe o MAC direto: `sudo TGEN_MAC=<mac-do-tgen> /local/repository/harness/run_l3fwd.sh privring-1024`.
3. **`tgen`, janela 2: varredura de taxas** (10–100% da line rate, 30 s cada, ~3,5 min):
   ```bash
   mkdir -p /mydata/results && python3 /local/repository/harness/trex/rate_sweep.py \
       --label privring-1024 --out /mydata/results/results.csv
   ```
4. **`dut`:** `Ctrl-C` no l3fwd. O log com os contadores finais fica em `/mydata/dpdk-research/results/`.

Repita os passos 2–4 para cada sistema. Os detalhes e os resultados da primeira rodada estão nas seções 16 e 17 do `CLAUDE.md`.
