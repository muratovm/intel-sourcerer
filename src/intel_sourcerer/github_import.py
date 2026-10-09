"""Import repository READMEs and release notes through GitHub's public API."""
import base64
from urllib.parse import urlsplit, quote
import httpx
from .media_import import save_item


async def import_github(database,url):
    parts = urlsplit(url).path.strip('/').split('/')
    if len(parts)<2 or not all(parts[:2]):
        raise ValueError('Use a GitHub repository or release link.')
    owner,repo = parts[:2]
    release = len(parts)>2 and parts[2]=='releases'
    api = 'https://api.github.com/repos/' + quote(owner,safe='')+'/'+quote(repo,safe='')
    if release:
        tag = parts[4] if len(parts)>4 and parts[3]=='tag' else None
        api += '/releases/tags/'+quote(tag,safe='') if tag else '/releases/latest'
    else:
        api += '/readme'
    async with httpx.AsyncClient(timeout=20,headers={'Accept':'application/vnd.github+json','User-Agent':'IntelSourcerer/0.1'}) as client:
        response = await client.get(api)
        if response.status_code!=200:
            raise ValueError('GitHub could not provide this public README or release (it may be unavailable or rate limited).')
        data=response.json()
    if release:
        markdown=data.get('body') or 'No release notes supplied.'
        title=f'{owner}/{repo} · {data.get("name") or data.get("tag_name") or "Release"}'
        identity=data.get('html_url') or url
    else:
        markdown=base64.b64decode(data['content']).decode('utf-8',errors='replace')
        title=f'{owner}/{repo} · README'
        identity=f'https://github.com/{owner}/{repo}'
    return save_item(database,identity,title,markdown,'repository','GitHub',owner)
