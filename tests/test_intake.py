"""L'entrée des documents ne doit jamais perdre ni altérer un original.

Ces tests portent sur la seule chose irréversible du pipeline : une facture
écrasée ou dédoublonnée à tort ne se récupère pas.
"""

from gramme.intake import fingerprint, fingerprints_in, store


def write(directory, name: str, content: bytes = b"facture"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path


class TestFingerprint:
    def test_the_same_content_gives_the_same_fingerprint(self, tmp_path):
        a = write(tmp_path, "a.pdf", b"identique")
        b = write(tmp_path, "b.pdf", b"identique")
        assert fingerprint(a) == fingerprint(b)

    def test_different_contents_give_different_fingerprints(self, tmp_path):
        a = write(tmp_path, "a.pdf", b"une facture")
        b = write(tmp_path, "b.pdf", b"une autre")
        assert fingerprint(a) != fingerprint(b)

    def test_a_large_file_is_read_in_blocks(self, tmp_path):
        # Dépasse la taille de bloc (64 Ko) pour couvrir la boucle de lecture.
        large = write(tmp_path, "scan.pdf", b"x" * 200_000)
        assert len(fingerprint(large)) == 64

    def test_the_index_ignores_foreign_files(self, tmp_path):
        write(tmp_path, "facture.pdf")
        write(tmp_path, ".DS_Store", b"bruit")
        assert len(fingerprints_in(tmp_path)) == 1


class TestStore:
    def test_it_copies_the_new_documents(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(source, "IMG_4821.jpg", b"metro")
        write(source, "IMG_4822.jpg", b"transgourmet")

        report = store(source.iterdir(), raw)
        assert len(report.stored) == 2
        assert sorted(f.name for f in raw.iterdir()) == ["IMG_4821.jpg", "IMG_4822.jpg"]

    def test_a_duplicate_is_set_aside(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(raw, "2026-08-12_metro.jpg", b"contenu")
        # Le client renvoie la même photo sous un autre nom.
        write(source, "IMG_4821.jpg", b"contenu")

        report = store(source.iterdir(), raw)
        assert not report.stored
        (offered, existing) = report.duplicates[0]
        assert existing.name == "2026-08-12_metro.jpg"
        assert len(list(raw.iterdir())) == 1

    def test_a_duplicate_inside_the_same_batch(self, tmp_path):
        # Deux copies identiques dans le dossier reçu : la seconde doit être vue
        # comme doublon de la première, pas rangée.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(source, "a.jpg", b"identique")
        write(source, "b.jpg", b"identique")

        report = store(source.iterdir(), raw)
        assert len(report.stored) == 1
        assert len(report.duplicates) == 1

    def test_the_same_name_with_different_contents_keeps_both(self, tmp_path):
        # Deux téléphones produisent chacun un IMG_0001.JPG. Empreintes
        # différentes : ce ne sont pas des doublons, il faut les garder.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(raw, "IMG_0001.jpg", b"premier")
        write(source, "IMG_0001.jpg", b"second")

        report = store(source.iterdir(), raw)
        assert len(report.stored) == 1
        assert sorted(f.name for f in raw.iterdir()) == ["IMG_0001.jpg", "IMG_0001_2.jpg"]
        assert (raw / "IMG_0001.jpg").read_bytes() == b"premier"  # jamais écrasé

    def test_the_content_is_copied_byte_for_byte(self, tmp_path):
        # Décision 4 : le document source est immuable.
        source, raw = tmp_path / "recu", tmp_path / "raw"
        original = write(source, "facture.pdf", b"%PDF-1.4 contenu binaire \x00\xff")

        store(source.iterdir(), raw)
        assert (raw / "facture.pdf").read_bytes() == original.read_bytes()

    def test_a_heic_is_kept_and_flagged(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(source, "IMG_4821.HEIC", b"heic")

        report = store(source.iterdir(), raw)
        assert [f.name for f in report.stored] == ["IMG_4821.HEIC"]
        assert [f.name for f in report.to_convert] == ["IMG_4821.HEIC"]

    def test_unknown_extensions_are_ignored(self, tmp_path):
        source, raw = tmp_path / "recu", tmp_path / "raw"
        write(source, "notes.txt", b"bla")
        write(source, ".DS_Store", b"bruit")

        report = store(source.iterdir(), raw)
        assert not report.stored
        assert len(report.ignored) == 2

    def test_the_destination_is_created_when_missing(self, tmp_path):
        source = tmp_path / "recu"
        write(source, "a.jpg")
        report = store(source.iterdir(), tmp_path / "pas" / "encore" / "la")
        assert len(report.stored) == 1
