"""L'entrée des documents ne doit jamais perdre ni altérer un original.

Ces tests portent sur la seule chose irréversible du pipeline : une facture
écrasée ou dédoublonnée à tort ne se récupère pas.
"""

from gramme.intake import classer, empreinte, empreintes_du_dossier


def ecrire(dossier, nom: str, contenu: bytes = b"facture"):
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    chemin.write_bytes(contenu)
    return chemin


class TestEmpreinte:
    def test_meme_contenu_meme_empreinte(self, tmp_path):
        a = ecrire(tmp_path, "a.pdf", b"identique")
        b = ecrire(tmp_path, "b.pdf", b"identique")
        assert empreinte(a) == empreinte(b)

    def test_contenu_different_empreinte_differente(self, tmp_path):
        a = ecrire(tmp_path, "a.pdf", b"une facture")
        b = ecrire(tmp_path, "b.pdf", b"une autre")
        assert empreinte(a) != empreinte(b)

    def test_gros_fichier_lu_par_blocs(self, tmp_path):
        # Dépasse la taille de bloc (64 Ko) pour couvrir la boucle de lecture.
        gros = ecrire(tmp_path, "scan.pdf", b"x" * 200_000)
        assert len(empreinte(gros)) == 64

    def test_index_ignore_les_intrus(self, tmp_path):
        ecrire(tmp_path, "facture.pdf")
        ecrire(tmp_path, ".DS_Store", b"bruit")
        assert len(empreintes_du_dossier(tmp_path)) == 1


class TestClasser:
    def test_copie_les_nouveaux(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(source, "IMG_4821.jpg", b"metro")
        ecrire(source, "IMG_4822.jpg", b"transgourmet")

        rapport = classer(source.iterdir(), raw)
        assert len(rapport.copies) == 2
        assert sorted(f.name for f in raw.iterdir()) == ["IMG_4821.jpg", "IMG_4822.jpg"]

    def test_doublon_ecarte(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(raw, "2026-08-12_metro.jpg", b"contenu")
        # Damien renvoie la même photo sous un autre nom.
        ecrire(source, "IMG_4821.jpg", b"contenu")

        rapport = classer(source.iterdir(), raw)
        assert not rapport.copies
        (propose, existant) = rapport.doublons[0]
        assert existant.name == "2026-08-12_metro.jpg"
        assert len(list(raw.iterdir())) == 1

    def test_doublon_a_l_interieur_du_meme_lot(self, tmp_path):
        # Deux copies identiques dans le dossier reçu : la seconde doit être vue
        # comme doublon de la première, pas copiée.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(source, "a.jpg", b"identique")
        ecrire(source, "b.jpg", b"identique")

        rapport = classer(source.iterdir(), raw)
        assert len(rapport.copies) == 1
        assert len(rapport.doublons) == 1

    def test_meme_nom_contenus_differents_conserves(self, tmp_path):
        # Deux téléphones produisent chacun un IMG_0001.JPG. Empreintes
        # différentes : ce ne sont pas des doublons, il faut les garder.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(raw, "IMG_0001.jpg", b"premier")
        ecrire(source, "IMG_0001.jpg", b"second")

        rapport = classer(source.iterdir(), raw)
        assert len(rapport.copies) == 1
        assert sorted(f.name for f in raw.iterdir()) == ["IMG_0001.jpg", "IMG_0001_2.jpg"]
        assert (raw / "IMG_0001.jpg").read_bytes() == b"premier"  # jamais écrasé

    def test_contenu_copie_a_l_identique(self, tmp_path):
        # Décision 4 : le document source est immuable.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        original = ecrire(source, "facture.pdf", b"%PDF-1.4 contenu binaire \x00\xff")

        classer(source.iterdir(), raw)
        assert (raw / "facture.pdf").read_bytes() == original.read_bytes()

    def test_heic_conserve_et_signale(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(source, "IMG_4821.HEIC", b"heic")

        rapport = classer(source.iterdir(), raw)
        assert [f.name for f in rapport.copies] == ["IMG_4821.HEIC"]
        assert [f.name for f in rapport.a_convertir] == ["IMG_4821.HEIC"]

    def test_extensions_inconnues_ignorees(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        ecrire(source, "notes.txt", b"bla")
        ecrire(source, ".DS_Store", b"bruit")

        rapport = classer(source.iterdir(), raw)
        assert not rapport.copies
        assert len(rapport.ignores) == 2

    def test_destination_creee_si_absente(self, tmp_path):
        source = tmp_path / "recu"
        ecrire(source, "a.jpg")
        rapport = classer(source.iterdir(), tmp_path / "pas" / "encore" / "la")
        assert len(rapport.copies) == 1
