# Immagine SoIA per PC (anteprima)

Fedora Silverblue (immutabile, aggiornamenti atomici) con dentro SoIA: Nova, i servizi
in sottofondo, Ollama, icone, scorciatoia Super+Spazio, controllo all'avvio (greenboot).

## Costruire l'immagine

```bash
podman build -t localhost/aios:44 -f image/Containerfile .     # dalla radice del repository
```

Su GitHub la costruisce la procedura «Immagine SoIA» (`.github/workflows/immagine.yml`) e la
pubblica come `ghcr.io/<utente>/aios:44`.

## Provarla senza reinstallare (da un Fedora Atomic: Silverblue, Kinoite…)

```bash
rpm-ostree rebase ostree-unverified-registry:ghcr.io/<utente>/aios:44
systemctl reboot
```

Il sistema precedente resta: se qualcosa non va, all'avvio si sceglie la voce precedente,
oppure `rpm-ostree rollback`.

## Chiavetta d'installazione

Il modo più semplice: su GitHub, **Actions › Immagine SoIA › Run workflow** (con «Crea la
chiavetta» spuntato). Dopo circa un'ora la ISO compare tra le **Releases** come «anteprima»,
divisa in pezzi da meno di 2 GB se serve: le istruzioni per riunirla, scriverla sulla chiavetta
e installare sono in [`RELEASE.md`](RELEASE.md).

In locale:

```bash
sudo podman run --rm -it --privileged --pull=newer \
  --security-opt label=type:unconfined_t \
  -v ./image/config.toml:/config.toml:ro -v ./uscita:/output \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  quay.io/centos-bootc/bootc-image-builder:latest \
  --type anaconda-iso --rootfs btrfs localhost/aios:44
```

Il file ISO finisce in `uscita/bootiso/install.iso`: si scrive su una chiavetta (per es. con
Fedora Media Writer) e si avvia il PC da lì. Lingua, tastiera e utente iniziale (aios/aios) sono
già impostati da [`config.toml`](config.toml); il disco lo scegli tu nell'installatore.

## Stato

Anteprima: il `Containerfile` non è ancora stato costruito e provato su un PC vero.
Prima della versione pubblica: firma dell'immagine (cosign) e `ostree-image-signed`
al posto di `ostree-unverified-registry`, utente iniziale creato dal benvenuto invece che
da `config.toml`.
