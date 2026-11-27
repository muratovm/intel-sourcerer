from intel_sourcerer.content import extract_article

PROSE = "<p>" + "OverWatch saw the actor stage tools on a relay host. " * 6 + "</p>"
PAGE = f"""<html><head>
<meta property="og:image" content="https://assets.crowdstrike.com/is/image/crowdstrikeinc/WebShareDefaultImage">
</head><body><main><div class="root">
<div class="cmp-wp-header blog_main_navigation">
  <a class="cmp-wp-logo crowdstrike_logo"><img src="https://assets.crowdstrike.com/is/content/crowdstrikeinc/RedLogoCS" alt="Crowdstrike"></a>
  <ul class="blog_navigation_right"><li><a href="/en-us/blog/featured-articles/">Featured</a>
    <ul class="horizontal_dropdown"><li><a href="/en-us/blog/other/"><div class="image">
      <img src="https://assets.crowdstrike.com/is/image/crowdstrikeinc/Blog-Other" alt="Another post"></div>
      <p>Another post</p></a></li></ul></li></ul>
</div>
<div class="container-wp container-wp--main-content-blog">
  <div class="cmp-wp-headline"><h1>VAPOR PANDA campaign</h1>
    <div class="post_image"><img src="https://assets.crowdstrike.com/is/image/crowdstrikeinc/Blog-Panda" alt=""></div></div>
  {PROSE}{PROSE}
  <div class="row recent_articles"><div class="recent_articles_item"><div class="post_image">
    <img src="https://assets.crowdstrike.com/is/image/crowdstrikeinc/Blog-Recent" alt="Recent post"></div>
    <a href="/en-us/blog/recent/">Recent post</a></div></div>
</div></div></main></body></html>"""


def test_crowdstrike_keeps_only_the_article_and_its_own_banner():
    html, markdown, media = extract_article(PAGE, "https://www.crowdstrike.com/en-us/blog/vapor-panda/")
    assert [item["url"] for item in media] == ["https://assets.crowdstrike.com/is/image/crowdstrikeinc/Blog-Panda"]
    assert markdown.startswith("# VAPOR PANDA campaign")
    for gone in ("Featured", "Another post", "Recent post", "RedLogoCS", "WebShareDefaultImage"):
        assert gone not in html
