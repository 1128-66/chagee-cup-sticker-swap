# 自检素材（全部是合成数据）

这里的文件**不是真实抓包**，是脚本按 `upload_request.har` 的格式合成出来的，
目的是让 `selftest_replace.py` / `selftest_mitm.py` **clone 下来就能跑**，
不依赖任何人的真实流量。

| 文件 | 说明 |
| --- | --- |
| `upload_request.har` | 一个合成的上传请求。token / cookie / 图片地址全是编造的 |
| `sample_artwork.png` | 618×927 的示例「作品」，自检时当替换图用 |

里面的假值一眼可辨：

- `qm-user-token` 是 `EXAMPLENotARealToken…` 拼出来的 86 字符
- `acw_tc` / `ssxmod_itna3` 都是 `EXAMPLE…` 开头
- 图片地址是 `images.qmai.cn/common/s00000/2026/01/01/00000000000000000000.png`

## 想用你自己的真实抓包

```bash
# 自检①
python3 selftest_replace.py --har /path/to/traffic.har

# 自检②
CUP_TEST_HAR=/path/to/traffic.har \
  mitmdump -s selftest_mitm.py --listen-host 127.0.0.1 --listen-port 18899
```

> 你自己的 HAR 里含真实登录态，**别提交**（`.gitignore` 已忽略 `traffic.har`）。
