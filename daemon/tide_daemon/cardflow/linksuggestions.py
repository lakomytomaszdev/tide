"""Małe adaptery PR/MR. Link wybiera endpoint, źródło wybiera wyłącznie lokalny ref."""
import ipaddress
import re
from urllib.parse import quote, urlsplit

from .hostingcli import HostingError, read_json

MAX_LINKS = 4
_HOST = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_PROJECT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
_SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def project_name(value):
    if (not isinstance(value, str) or len(value) > 1024 or not _PROJECT.fullmatch(value)
            or any(p in (".", "..") or p.startswith("-") for p in value.split("/"))):
        raise ValueError()
    return value


def https_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError()
    url = urlsplit(value)
    host = url.hostname or ""
    if (url.scheme != "https" or url.username is not None or url.password is not None
            or url.port not in (None, 443) or not _HOST.fullmatch(host)
            or host == "localhost" or host.endswith(".localhost")):
        raise ValueError()
    try:
        if ipaddress.ip_address(host).is_loopback:
            raise ValueError()
    except ValueError:
        if re.fullmatch(r"[0-9.]+", host):
            raise ValueError()
    return host, url.path, url


def repo_identity(value):
    """SSH, scp i HTTPS dają tę samą tożsamość, bez wypisywania userinfo."""
    if not isinstance(value, str):
        raise ValueError()
    if "://" not in value:
        match = re.fullmatch(r"(?:[^/@:\s]+@)?([A-Za-z0-9.-]+):([^\s]+)", value)
        if not match:
            raise ValueError()
        host, path = match.groups()
    else:
        url = urlsplit(value)
        if url.scheme not in ("https", "ssh") or not url.hostname or url.query or url.fragment:
            raise ValueError()
        if url.scheme == "https" and url.port not in (None, 443):
            raise ValueError()
        host, path = url.hostname, url.path.lstrip("/")
    host = host.lower()
    if not _HOST.fullmatch(host):
        raise ValueError()
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return host, project_name(path)


def same_project(a, b, provider):
    # GitHub ignoruje wielkość liter; ścieżki GitLaba pozostają dokładne.
    return a[0] == b[0] and (a[1].casefold() == b[1].casefold() if provider == "gh" else a[1] == b[1])


def parse_link(value):
    host, path, url = https_url(value)
    if url.query:  # Zapytania mogą zawierać poświadczenia, nigdy ich nie przechowujemy.
        raise ValueError()
    match = re.fullmatch(r"/([^/]+/[^/]+)/pull/([1-9][0-9]{0,9})/?", path)
    provider = "gh"
    if not match:
        match = re.fullmatch(r"/(.+)/-/merge_requests/([1-9][0-9]{0,9})/?", path)
        provider = "glab"
    if not match:
        raise ValueError()
    project, number = match.groups()
    project_name(project)
    if provider == "gh":
        project = project.casefold()
    marker = "pull" if provider == "gh" else "-/merge_requests"
    return {"provider": provider, "host": host, "project": project, "number": int(number),
            "link": f"https://{host}/{project}/{marker}/{number}"}


def branch_name(value):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or re.search(r"[\s\x00-\x1f\x7f]", value)):
        raise ValueError()
    return value


def metadata(link, repo):
    provider, host, project, number = (link[k] for k in ("provider", "host", "project", "number"))
    if provider == "gh":
        data = read_json("gh", host, f"repos/{project}/pulls/{number}", repo)
        if data["number"] != number or parse_link(data["html_url"]) != link:
            raise ValueError()
        target, source = data["base"], data["head"]
        target_repo, source_repo = target["repo"], source["repo"]
        target_id = repo_identity(target_repo["html_url"])
        source_id = repo_identity(source_repo["html_url"])
        if (not same_project(target_id, (host, project), provider)
                or target_id[1].casefold() != project_name(target_repo["full_name"]).casefold()
                or source_id[1].casefold() != project_name(source_repo["full_name"]).casefold()
                or source_id[0] != host):
            raise ValueError()
        source_project, source_branch, source_commit, target_branch = source_id[1].casefold(), source["ref"], source["sha"], target["ref"]
    else:
        data = read_json("glab", host, f"projects/{quote(project, safe='')}/merge_requests/{number}", repo)
        if data["iid"] != number or parse_link(data["web_url"]) != link:
            raise ValueError()
        source_id, target_id = data["source_project_id"], data["target_project_id"]
        if type(source_id) is not int or source_id <= 0 or type(target_id) is not int or target_id <= 0:
            raise ValueError()
        # Identyfikatory są danymi liczbowymi z API; nigdy URL-em do pobrania.
        identities = {}
        for project_id in dict.fromkeys((target_id, source_id)):
            item = read_json("glab", host, f"projects/{project_id}", repo)
            identity = repo_identity(item["web_url"])
            if (item["id"] != project_id or identity[0] != host
                    or identity[1] != project_name(item["path_with_namespace"])):
                raise ValueError()
            identities[project_id] = identity
        if identities[target_id] != (host, project):
            raise ValueError()
        source_project, source_branch, source_commit, target_branch = (identities[source_id][1], data["source_branch"],
                                                                    data["sha"], data["target_branch"])
    if not isinstance(source_commit, str) or not _SHA.fullmatch(source_commit):
        raise ValueError()
    return {k: link[k] for k in ("host", "project", "link")} | {
        "sourceProject": source_project, "sourceBranch": branch_name(source_branch),
        "sourceCommit": source_commit.lower(), "targetBranch": branch_name(target_branch)}


def remote_config(git, repo):
    result = git.run(repo, "config", "--null", "--get-regexp",
                     r"^(remote\..*\.(url|fetch)|branch\..*\.(remote|merge))$")
    remotes, upstreams = {}, {}
    if result.code not in (0, 1):
        raise HostingError("Nie można odczytać tożsamości remote. Wybierz bazę ręcznie.")
    for entry in result.out.split("\0"):
        key, sep, value = entry.partition("\n")
        match = re.fullmatch(r"remote\.(.+)\.(url|fetch)", key)
        if sep and match:
            remote, field = match.groups()
            remotes.setdefault(remote, {}).setdefault(field, []).append(value)
        match = re.fullmatch(r"branch\.(.+)\.(remote|merge)", key)
        if sep and match:
            branch, field = match.groups()
            upstreams.setdefault(branch, {}).setdefault(field, []).append(value)
    for config in remotes.values():
        try:
            # Kilka URL-i jest niejednoznaczne, nie zgadujemy pierwszego.
            config["identity"] = repo_identity(config["url"][0]) if len(config.get("url", [])) == 1 else None
        except ValueError:
            config["identity"] = None
    for branch, upstream in upstreams.items():
        remote, merge = upstream.get("remote", []), upstream.get("merge", [])
        if len(remote) == len(merge) == 1 and remote[0] in remotes:
            remotes[remote[0]].setdefault("locals", []).append(("refs/heads/" + branch, merge[0]))
    return remotes


def tracking_refs(config, branch):
    source = "refs/heads/" + branch
    refs = []
    for spec in config.get("fetch", []):
        if spec.startswith("^"):
            pattern = spec[1:]
            if re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), source):
                return []
            continue
        src, sep, dst = spec.lstrip("+").partition(":")
        if not sep:
            continue
        if "*" not in src and src == source:
            refs.append(dst)
        elif src.count("*") == dst.count("*") == 1:
            match = re.fullmatch(re.escape(src).replace(r"\*", "(.*)"), source)
            if match:
                refs.append(dst.replace("*", match.group(1)))
    return [ref for ref in refs if ref.startswith("refs/remotes/")]


def claims_ref(config, ref):
    for spec in config.get("fetch", []):
        if spec.startswith("^"):
            continue
        _, sep, destination = spec.lstrip("+").partition(":")
        if sep and re.fullmatch(re.escape(destination).replace(r"\*", ".*"), ref):
            return True
    return False


def candidates_for(meta, provider, branches, remotes):
    source = (meta["host"], meta["sourceProject"])
    matching = [c for c in remotes.values() if c["identity"] and same_project(c["identity"], source, provider)]
    possible = {ref for c in matching for ref in tracking_refs(c, meta["sourceBranch"])}
    possible.update(ref for c in matching for ref, merge in c.get("locals", [])
                    if merge == "refs/heads/" + meta["sourceBranch"])
    # Ref wspólny z innym repozytorium nie dowodzi pochodzenia źródła.
    foreign = {ref for ref in possible for c in remotes.values() if c not in matching and claims_ref(c, ref)}
    possible -= foreign
    found = [b for b in branches if b["ref"] in possible]
    common = {**meta, "source": "links", "strength": "explicit", "evidence": []}
    if not found:
        reason = ("Brak skonfigurowanego remote repozytorium źródłowego." if not matching else
                  "Brak jednoznacznego lokalnego refu źródła. Nie pobrano danych.")
        return [{**common, "ref": "", "name": meta["sourceBranch"], "commit": "", "localCommit": "",
                 "selectable": False, "stale": False, "reason": reason}]
    return [{**b, **common, "localCommit": b["commit"], "stale": b["commit"] != meta["sourceCommit"],
             "reason": "Lokalny ref jest nieaktualny względem API. Możesz jawnie użyć stanu lokalnego."
             if b["commit"] != meta["sourceCommit"] else ""} for b in found]


def suggest_links(git, repo, branches, description, text):
    remotes = remote_config(git, repo)
    candidates, warnings, seen = [], [], set()
    for raw in re.findall(r"https?://[^\s<>`\"\[\]]+", description + "\n" + text):
        try:
            link = parse_link(raw.rstrip(".,;)"))
        except ValueError:
            if "Nieobsługiwany lub niebezpieczny link. Dozwolone są rozpoznane HTTPS PR/MR." not in warnings:
                warnings.append("Nieobsługiwany lub niebezpieczny link. Dozwolone są rozpoznane HTTPS PR/MR.")
            continue
        if link["link"] in seen:
            continue
        seen.add(link["link"])
        if len(seen) > MAX_LINKS:
            warnings.append("Sprawdzono najwyżej 4 linki. Ogranicz opis lub wybierz bazę ręcznie.")
            break
        if not any(c["identity"] and same_project(c["identity"], (link["host"], link["project"]), link["provider"])
                   for c in remotes.values()):
            warnings.append("Link PR/MR nie odpowiada skonfigurowanemu remote projektu. Nie odczytano hostingu.")
            continue
        try:
            meta = metadata(link, repo)
            candidates.extend(candidates_for(meta, link["provider"], branches, remotes))
        except HostingError as exc:
            warnings.append(str(exc))
        except (ValueError, KeyError, TypeError, AttributeError):
            warnings.append("Metadane PR/MR są niepełne lub nie potwierdzają tożsamości źródła.")
    if not seen:
        warnings.append("Brak rozpoznanego linku HTTPS do PR/MR.")
    return {"candidates": candidates[:32], "warnings": warnings[:20]}
