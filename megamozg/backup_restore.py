"""Restore a verified backup into a new directory, leaving existing data untouched."""
from contextlib import closing
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile

from .core import ValidationError


def restore_backup(archive_path, destination):
    target = Path(destination).resolve()
    if target.exists():
        raise ValidationError("Для восстановления укажите новую, ещё не существующую папку")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="brainalot-restore-", dir=target.parent) as temporary:
        stage = Path(temporary) / "data"
        stage.mkdir()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > 100000 or sum(m.file_size for m in members) > 2 * 1024**3:
                    raise ValidationError("Архив превышает лимит восстановления (2 GiB / 100000 файлов)")
                names = set()
                for member in members:
                    name = PurePosixPath(member.filename)
                    path = (stage / member.filename).resolve()
                    if (not path.is_relative_to(stage) or name.is_absolute() or ".." in name.parts
                            or "\\" in member.filename or ":" in member.filename):
                        raise ValidationError("Небезопасный путь в архиве")
                    permitted = name.parts and name.parts[0] in {"vault", "attachments", "chat-files"}
                    permitted |= member.filename in {"state/journal.sqlite3", "state/shared.sqlite3", "state/time.sqlite3", "state/chats.sqlite3"}
                    if not permitted or member.filename in names:
                        raise ValidationError("Неожиданный или повторный файл в архиве")
                    names.add(member.filename)
                    if member.is_dir():
                        path.mkdir(parents=True, exist_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, path.open("xb") as output:
                            shutil.copyfileobj(source, output)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise ValidationError("Повреждённый или зашифрованный архив") from exc
        if not (stage / "state/journal.sqlite3").is_file() or not (stage / "vault").is_dir():
            raise ValidationError("В архиве отсутствует библиотека или журнал")
        for database in (stage / "state").glob("*.sqlite3"):
            try:
                with closing(sqlite3.connect(database)) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValidationError("Повреждена база данных: " + database.name)
            except sqlite3.DatabaseError as exc:
                raise ValidationError("Неверная база данных: " + database.name) from exc
        # Same-volume rename publishes only the completely verified restored tree.
        stage.rename(target)
    return {"path": str(target), "source_untouched": str(Path(archive_path).resolve()), "token_regenerated_on_start": True}
