# Research source catalogue

Reviewed October 1, 2026. Twenty sources total, retaining the four initial sources. This is a curated starting set rather than an objective top-20 ranking. Selection favors first-party technical findings, incident visibility, and complementary coverage over news aggregation. Publication timing varies; RSS polling discovers published research rather than guaranteeing immediate alerts.

## Sources

| Source | Research focus / collection notes | Collection |
| --- | --- | --- |
| [OpenAI](https://openai.com/index/disrupting-malicious-uses-of-ai/) | Dedicated misuse reports and case studies hub; RSS includes all OpenAI news and needs relevance filtering. | RSS verified |
| [Google Threat Analysis Group](https://blog.google/security/) | Legacy TAG index redirects to the broader Security blog. This feed includes non-TAG security articles. | RSS verified |
| [Anthropic](https://www.anthropic.com/threat-intelligence) | Dedicated threat intelligence reports index. Official RSS not verified; requires a future listing adapter. | Listing adapter needed |
| [Google Threat Intelligence](https://cloud.google.com/blog/topics/threat-intelligence) | Separate Google Cloud threat intelligence publication, retained from the initial project scope. | RSS verified |
| [Microsoft Threat Intelligence](https://www.microsoft.com/en-us/security/blog/topic/threat-intelligence/) | Nation-state campaigns, ransomware, identity abuse, and telemetry-backed investigations. Working feed covers a broader publication than this research index; relevance filtering is needed. | RSS verified |
| [Palo Alto Networks Unit 42](https://unit42.paloaltonetworks.com/) | Original malware analysis, active exploitation, cloud attacks, and incident-response research. | RSS verified |
| [Cisco Talos](https://blog.talosintelligence.com/) | Campaign investigations, malware reverse engineering, vulnerabilities, and incident-response findings. | RSS verified |
| [SentinelLABS](https://www.sentinelone.com/labs/) | Technical malware and threat-actor investigations, including espionage and macOS threats. | RSS verified |
| [CrowdStrike Threat Intelligence](https://www.crowdstrike.com/en-us/blog/) | Adversary tracking, intrusion campaigns, and frontline threat intelligence. The current blog and working feed include broader product/company posts; relevance filtering is needed. | RSS verified |
| [Kaspersky Securelist](https://securelist.com/) | Original APT, malware, and cybercrime research with technical artifacts. | RSS verified |
| [ESET WeLiveSecurity](https://www.welivesecurity.com/en/) | APT campaigns, malware reverse engineering, and regional threat research; broader news also appears. | RSS verified |
| [Sophos X-Ops](https://www.sophos.com/en-us/blog/category/threat-research) | Ransomware, incident-response findings, and technical malware research. Current Sophos blog feed is verified; includes broader posts. The former news.sophos.com endpoint timed out. | RSS verified |
| [Proofpoint Threat Insight](https://www.proofpoint.com/us/blog/threat-insight) | Email-delivered campaigns, social engineering, cybercrime, and threat-actor tracking. Working feed covers a broader publication than this research index; relevance filtering is needed. | RSS verified |
| [Check Point Research](https://research.checkpoint.com/) | Original malware, vulnerability, cybercrime, and nation-state campaign analysis. | RSS verified |
| [Trend Micro Research](https://www.trendmicro.com/en_us/research.html) | Malware, exploitation, cloud threats, and campaign research. No working official RSS verified; listing adapter required. | Listing adapter needed |
| [Elastic Security Labs](https://www.elastic.co/security-labs) | Technical malware research, intrusion analysis, and detection engineering. No working official RSS verified; listing adapter required. | Listing adapter needed |
| [Rapid7 Research](https://www.rapid7.com/blog/tag/research/) | Vulnerability disclosures, exploitation analysis, and attacker research. Working feed covers a broader publication than this research index; relevance filtering is needed. | RSS verified |
| [Huntress](https://www.huntress.com/blog) | Frontline investigations of active exploitation, ransomware, and tradecraft; broader company posts also appear. | RSS verified |
| [Red Canary Threat Intelligence](https://www.zscaler.com/blogs) | Behavior-focused threat research, malware campaigns, and detection intelligence. Working feed covers a broader publication than this research index; relevance filtering is needed. | RSS verified |
| [Recorded Future Insikt Group](https://www.recordedfuture.com/research) | Original espionage, cybercrime, infrastructure, and geopolitical threat investigations. Working feed covers a broader publication than this research index; relevance filtering is needed. | RSS verified |

## Verification and limits

- Existing OpenAI, Google Security, and Google Threat Intelligence feeds passed live checks earlier in this session. Newly configured feeds were fetched and parsed with the project collector; only working feeds were saved.
- Sophos migrated to www.sophos.com/en-us/blog; its current feed fetched nine articles. The old news.sophos.com endpoints timed out. CrowdStrike’s proposed category URL returned 404, so the working main blog is configured.
- Anthropic, Elastic Security Labs, and Trend Micro Research remain enabled discovery sources without RSS URLs. The current CLI reports them as skipped. Their inclusion does not mean listing collection is implemented.
- Google TAG’s former index redirects to Google Security; keep that broad feed distinct from Google Cloud Threat Intelligence.
- Feed availability and source quality should be reviewed over time. Empty, blocked, or moved endpoints should surface through source health rather than silently removing publishers.
- Publisher assertions, attribution, and severity remain claims to assess. Cross-source confirmation and original evidence matter.

## Source grouping

The master config is grouped by one label, `source_type`: `ai_lab` for OpenAI and Anthropic, and `security_vendor` for the remaining 18 research publications, including Google's and Microsoft's security teams. This classifies the publishing organization; it does not assign article topics or collection modes.
