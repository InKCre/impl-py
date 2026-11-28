# Live multimodal lexical acceptance

This opt-in journey uses NASA's public `GPM: Meet the Team: Dave McComas`
asset as one coherent image, audio, video, and authored-subtitle authority. The
downloaded and derived files live in ignored `.assets/`; production code sees
only ordinary bytes through PostgreSQL binary Storage and exact Resolvers.

The preparation script pins the NASA asset URLs, observed ETags, and SHA-256
digests. It extracts one real frame and the real audio track, then remuxes the
authored WebVTT into Matroska without changing the source streams. NASA's media
usage guidance remains the license and attribution authority:
<https://www.nasa.gov/nasa-brand-center/images-and-media/>.

选用独占的临时 PostgreSQL 集群和真实 Alibaba Model Studio 模型。测试用初始化该集群的角色密码创建已迁移的模板，并从模板克隆本次场景的数据库。使用 Core 角色连接、同一集群的管理员连接及以下变量运行。

```shell
INKCRE_TEST_DATABASE_URL='...' \
INKCRE_TEST_POSTGRES_ADMIN_URL='...' \
CORE_DATABASE_PASSWORD='...' \
POSTGREST_DATABASE_PASSWORD='...' \
INKCRE_ACCEPTANCE_AI_API_KEY='...' \
INKCRE_ACCEPTANCE_AI_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1' \
INKCRE_ACCEPTANCE_MULTIMODAL_MODEL='qwen3.5-omni-flash' \
pdm run pytest tests/lexical_retrieval/acceptance/test_multimodal_live.py -q
```

The test deliberately does not read project-local credential aliases. CI or a
human operator must opt in with the canonical acceptance environment names.

