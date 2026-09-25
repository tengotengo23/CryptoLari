# CryptoLari სერვერზე: კვანძი + ექსპლორერი + HTTPS

ეს ინსტრუქცია CryptoLari-ს მუდმივ, ინტერნეტში ხელმისაწვდომ კვანძს და ექსპლორერს
აყენებს. ასეთი კვანძი სხვებისთვის „შესასვლელი კარია“ (seed node): მას უერთდებიან,
ექსპლორერში კი ყველა ხედავს ბლოკებს.

**რა გჭირდება:**

* Ubuntu 22.04 ან 24.04 VPS, 2 GB RAM (Hetzner, DigitalOcean, Contabo და ა.შ.; ~$5–10/თვე);
* დომენი (სურვილისამებრ, HTTPS-ისთვის), მაგალითად `explorer.example.ge`.

> ⚠️ systemd-ის და Caddy-ის ფაილები ამ რეპოზიტორიის ტესტებში არ მოწმდება.
> პირველად ყველაფერი testnet-ზე გაუშვი.

## 1. მომხმარებელი და კოდი

```bash
sudo adduser --disabled-password --gecos "" cryptolari
sudo -iu cryptolari
git clone https://github.com/tengotengo23/CryptoLari.git
cd CryptoLari
git checkout claude/hopeful-mayer-l05sqi     # ან main, როცა ცვლილებები იქ იქნება
```

## 2. აწყობა და პირველი გაშვება

`cryptolari` მომხმარებელს `sudo`-ს უფლება არ აქვს, ამიტომ ბიბლიოთეკები ჯერ შენი
ჩვეულებრივი მომხმარებლით დააყენე (`exit` → `sudo apt-get install ...`,
[README](../../../README.md)-ის სიიდან). შემდეგ:

```bash
sudo -iu cryptolari
cd CryptoLari
contrib/cryptolari/quickstart.sh --main --skip-deps     # testnet-ისთვის: --test
src/cryptolari-cli stop                                  # შემდეგ systemd მართავს
exit
```

## 3. ავტომატური გაშვება (systemd)

```bash
sudo cp /home/cryptolari/CryptoLari/contrib/cryptolari/deploy/cryptolarid.service /etc/systemd/system/
sudo cp /home/cryptolari/CryptoLari/contrib/cryptolari/deploy/cryptolari-explorer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cryptolarid cryptolari-explorer
systemctl status cryptolarid cryptolari-explorer
```

Testnet-ისთვის ორივე ფაილში შეცვალე: `cryptolarid`/`cryptolari-cli`-ს დაუმატე
`-testnet`, ექსპლორერში კი `--network main` → `--network test`.

## 4. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 9955/tcp      # CryptoLari P2P (testnet: 19955)
sudo ufw allow 80,443/tcp    # ექსპლორერი HTTPS-ით
sudo ufw enable
```

RPC პორტი (9954) **არ გახსნა**: ის მხოლოდ სერვერის შიგნით უნდა იყოს ხელმისაწვდომი.

## 5. HTTPS (Caddy)

1. დომენის DNS-ში შექმენი A ჩანაწერი, რომელიც სერვერის IP-ზე მიუთითებს.
2. დააყენე და დააკონფიგურირე Caddy:

```bash
sudo apt-get install -y caddy
sudo cp /home/cryptolari/CryptoLari/contrib/cryptolari/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # explorer.example.ge → შენი დომენი
sudo systemctl reload caddy
```

Caddy HTTPS სერტიფიკატს თვითონ მოიპოვებს და განაახლებს. ექსპლორერი გაიხსნება
`https://<შენი დომენი>/`-ზე.

## 6. ონკანის შევსება

ონკანი კვანძის საფულიდან იხდის. შეავსე ის მაინინგით ან გადარიცხვით:

```bash
sudo -iu cryptolari
cd CryptoLari
src/cryptolari-cli getaccountaddress ""    # ამ მისამართზე გადმორიცხე CLARI
src/cryptolari-cli getbalance
```

ლიმიტები `cryptolari-explorer.service`-ში იცვლება: `--faucet-amount`,
`--faucet-interval`, `--faucet-daily-limit`. ონკანის გამორთვა: `--no-faucet`.

## 7. სხვები როგორ შემოგიერთდებიან

```bash
src/cryptolarid -daemon -addnode=<სერვერის IP>:9955
```

## შემოწმება და პრობლემები

```bash
journalctl -u cryptolarid -f              # კვანძის ლოგი
journalctl -u cryptolari-explorer -f      # ექსპლორერის ლოგი
sudo -iu cryptolari CryptoLari/src/cryptolari-cli getpeerinfo
```

* **ექსპლორერი „cannot reach the node“ შეცდომას წერს:** კვანძი ჯერ ირთვება.
  დაელოდე 1 წუთს.
* **„ტრანზაქცია ვერ მოიძებნა“:** `~/.cryptolari/cryptolari.conf`-ში უნდა ეწეროს
  `txindex=1`. თუ ეს შეცვალე, კვანძი ერთხელ `-reindex`-ით გადატვირთე.
* **Backup:** საფულე (`~/.cryptolari/wallet.dat`) რეგულარულად სხვაგან დააკოპირე.
