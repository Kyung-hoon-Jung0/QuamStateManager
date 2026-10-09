"""Confidential-name guard without storing names in repository text."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

NAME_DIGESTS = frozenset((
    'c2e7e7b1d3688ea1f82ea53b3fda3a53b86e46a92488d88e2e50ffafd4ef3272',
    '62bfbd452ae09b4bddff99ff0fac0ad6cbfddbbcde0f56595be6e6f381b69089',
    'd1fbaf47a485bd25d8661c9b5edfde65dce6f20b516a815e57822552214a039c',
    '075b2fb09cb081cc20244b4f273535309fd32f88f622e5afe37cff4ae0c2dc08',
    '08dc9a0b95dd772053ee1a2b77c57083507e6fe1986543e5be576d858fd537cf',
    '1475001d1abb7d41064af695fd1420803699ac5ca718787f7dbeb00dd0be58ce',
    '1508b697895abf03d55c3841f59236ab92c9ba6ba89795c8337fcf392fdee8b4',
    '1d5ba75190c3022c393e3a4d7b5f6d2ce0e55913d877d9a5f2c2907910420107',
    '1fcff1fe7e601e10bb94291b44e0c531fd855f5c9335805f1f7865b576c21828',
    '21a40c5e7fb98711bfe962c22410ec7f79fe1e70c47e29c8ff6db5f1a792addd',
    '2516f118eb7e785e3667f0e1e544b99ccfa321af7c24bb102ffcfcd9f1b44355',
    '26ae784d194a5760464348329af4eb9fca2b27bbf823742c968a61543e3a1153',
    '28129291ab52608f86a2dbb0a53c3ef4a77b1cfcebe42f055945b15b083ef1f8',
    '2bd806c97f0e00af1a1fc3328fa763a9269723c8db8fac4f93af71db186d6e90',
    '30a7b7fe9392918bb60c14ddd95572c4d617d4179ca5a84a3295107149233ddb',
    '48c00660326ce73ac6ed3e7461b75e8698198e885037397fb0062816d1551d20',
    '4c26d9074c27d89ede59270c0ac14b71e071b15239519f75474b2f3ba63481f5',
    '5412ff962cc85eca6a851cd012744dc97279aafc8fca86b7df54112922257034',
    '552f1ae0cc0738624eeea2e1813cb7836c5e5409b36436d728daa01e48e0ba51',
    '592559ec86b4d92478ae765c821c5788cd9c85e1d1dd7db9d8b508753aa927a8',
    '658d283feab1e2a2600414e9b4776a167dc3962b4fdc39a1612920d39a2ed6ad',
    '668e2b73ac556a2f051304702da290160b29bad3392ddcc72074fefbee80c55a',
    '66f21f21021cceac4577c8b52ff358101eeedf318f9555883fe9894e1502c75b',
    '692b3a9c31e1f0745cd7aa3d906c7bff14259f0c9a843ec734ee3590a7e0f1e9',
    '7125ef4c90a674652758c50c937d3fc68c231a3ed695b642f922bd269ed2bd28',
    '727618252c6b92dc55fa6d76983528b24cf6f7cf76107ac7bb6fb4dff991fe40',
    '78859a2f30ab14b3e8db15c97d1bbf50869d3816e9ad3955940674d4ddbe849e',
    '7974a73c9d6b6a949bba029a05e1787c63b2ded98c9af3052308e07ec94b2015',
    '8071797f5df0a60a64688be1f3190f4316096944e3f95819a212708a3fc06cfe',
    '81b637d8fcd2c6da6359e6963113a1170de795e4b725b84d1e0b4cfd9ec58ce9',
    '92effc9f1855b249713d8418c56a9144db2837d419f505ec3a0bb98d1bb27ec1',
    '93306d31495a093c4c3e20ddcbb3b4d0eb21bf5adb0f7bd278a0c65df847e61f',
    '93f63eb1a1ff6f35106bf18031840919d6638d900c930179293bbea07ecf972c',
    '968e2d5b08687bf42997461cbdef6c844eabbf04f440cee888c95b864c2a4bcc',
    '9740e9e8d7c2065c25016b96a661e1bbd06911a01488e6d3e633f963a593c4ac',
    '97f5d79d855e27fa125b43cdd0b8ef43636c46478e634f07ecbb08a6a374ed7b',
    '995755fbb48152a749cea2af5d7988517a8fbe0b57a5bbde943f461da823260a',
    'ad6cfb0346aa0ab6682d3ee1d0d0e07d12b63097450a5800b1ec5d8d830f1980',
    'b2a64e91c0af7ad69232008d0f180414003e25fb109e2fc2ef637b1fb06a4021',
    'd38f2e91c6731aaf5a463fd7973eaf6d9826c27b53477cc30a4d763d4c599419',
    'db5759d7532f454490fea01f85e49526e07e2f009a6ea0fd68733023e02e3d53',
    'dec48f55e7a9154e5e57ae51af01984612e15c7390fc1a90a0ec58ac9ac0b344',
    'e5b64007f67a5e260d754f2f5da9d0f6b498b20ce010a9353932360fa031b1b4',
    'ed737194adc4a3a14f7b7ec4843c38d132a38e5ac9206bc94593012848f44a12',
))


def contains_forbidden_name(text: str) -> bool:
    for token in re.findall(r"[^\W_]+(?:_[^\W_]+)*", text.lower()):
        parts = token.split("_")
        for start in range(len(parts)):
            for end in range(start + 1, len(parts) + 1):
                candidate = "_".join(parts[start:end])
                if hashlib.sha256(candidate.encode("utf-8")).hexdigest() in NAME_DIGESTS:
                    return True
    return False


def assert_clean(files: list[Path]) -> None:
    offenders = []
    for file in files:
        if contains_forbidden_name(file.name) or (file.suffix.lower() != ".png" and
                contains_forbidden_name(file.read_text(encoding="utf-8", errors="replace"))):
            offenders.append(str(file))
    assert not offenders, offenders[:10]
