# Immagine AIOS per PC (anteprima)

Fedora Silverblue (immutabile, aggiornamenti atomici) con dentro AIOS: Nova, i servizi
in sottofondo, Ollama, icone, scorciatoia Super+Spazio, controllo all'avvio (greenboot).

## Costruire l'immagine

```bash
podman build -t localhost/aios:42 -f image/Containerfile .     # dalla radice del repository
```

Su GitHub la costruisce la procedura «Immagine AIOS» (`.github/workflows/immagine.yml`) e la
pubblica come `ghcr.io/<utente>/aios:42`.

## Provarla senza reinstallare (da un Fedora Atomic: Silverblue, Kinoite…)

```bash
rpm-ostree rebase ostree-unverified-registry:ghcr.io/<utente>/aios:42
systemctl reboot
```

Il sistema precedente resta: se qualcosa non va, all'avvio si sceglie la voce precedente,
oppure `rpm-ostree rollback`.

## Chiavetta d'installazione

```bash
sudo podman run --rm -it --privileged --pull=newer \
  --security-opt label=type:unconfined_t \
  -v ./image/config.toml:/config.toml:ro -v ./uscita:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type anaconda-iso --rootfs btrfs localhost/aios:42
```

Il file ISO finisce in `uscita/bootiso/install.iso`: si scrive su una chiavetta (per es. con
Fedora Media Writer) e si avvia il PC da lì.

## Stato

Anteprima: il `Containerfile` non è ancora stato costruito e provato su un PC vero.
Prima della versione pubblica: firma dell'immagine (cosign) e `ostree-image-signed`
al posto di `ostree-unverified-registry`, utente iniziale creato dal benvenuto invece che
da `config.toml`.
