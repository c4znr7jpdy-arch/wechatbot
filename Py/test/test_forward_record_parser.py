import html
import importlib.util
import sys
import threading
import unittest
from pathlib import Path


PY_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wechat_bridge_main", PY_DIR / "main.py")
wechat_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wechat_bridge
SPEC.loader.exec_module(wechat_bridge)


def build_forward_card(record_info: str, title: str = "测试聊天记录") -> str:
    encoded_record = html.escape(record_info, quote=False)
    return (
        "<msg><appmsg>"
        f"<title>{title}</title><type>19</type>"
        f"<recorditem><![CDATA[{encoded_record}]]></recorditem>"
        "</appmsg></msg>"
    )


class ForwardRecordParserTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(wechat_bridge.WeChatServiceHandler)

    def test_full_record_extracts_text_and_ordered_images(self):
        record_info = """
        <recordinfo><desc>摘要</desc><datalist count="3">
          <dataitem datatype="1">
            <sourcename>甲</sourcename><sourcetime>2026-7-19 下午11:52</sourcetime>
            <datadesc>先看这张图</datadesc>
          </dataitem>
          <dataitem datatype="2">
            <sourcename>甲</sourcename>
            <srcmsgcontent>&lt;msg&gt;&lt;img aeskey=&quot;key-1&quot; cdnmidimgurl=&quot;mid-1&quot; cdnthumburl=&quot;thumb-1&quot;/&gt;&lt;/msg&gt;</srcmsgcontent>
          </dataitem>
          <dataitem datatype="2">
            <sourcename>乙</sourcename>
            <cdnthumburl>thumb-2</cdnthumburl><cdnthumbkey>key-2</cdnthumbkey>
            <srcmsgcontent>&lt;msg&gt;&lt;img cdnbigimgurl=&quot;big-2&quot;/&gt;&lt;/msg&gt;</srcmsgcontent>
          </dataitem>
        </datalist></recordinfo>
        """
        payload = self.handler._parse_forward_record_payload(build_forward_card(record_info))

        self.assertTrue(payload["complete"])
        self.assertEqual(payload["item_count"], 3)
        self.assertIn("[2026-7-19 下午11:52] 甲: 先看这张图", payload["text"])
        self.assertIn("甲: [图片1]", payload["text"])
        self.assertIn("乙: [图片2]", payload["text"])
        self.assertEqual(payload["images"][0]["aes_key"], "key-1")
        self.assertEqual(payload["images"][0]["file_id_mid"], "mid-1")
        self.assertEqual(payload["images"][1]["aes_key"], "key-2")
        self.assertEqual(payload["images"][1]["file_id_big"], "big-2")

    def test_quoted_compact_record_still_returns_readable_summary(self):
        record_info = (
            "<recordinfo><desc>卡兹克: 啊？？\n卡兹克: [图片]\n"
            "Atom&amp;amp;原子君: 这是什么道理？</desc>"
            "<datalist count=\"0\"/></recordinfo>"
        )
        inner_card = build_forward_card(record_info, "卡兹克的聊天记录")
        quoted = (
            "<msg><appmsg><title>@姜小妹 分析一下</title><type>57</type><refermsg>"
            f"<content>{html.escape(inner_card)}</content>"
            "<displayname>祈</displayname><svrid>2305102652627502401</svrid><type>49</type>"
            "</refermsg></appmsg></msg>"
        )

        parsed_quote = self.handler._parse_11061_xml(quoted)
        payload = self.handler._parse_forward_record_payload(quoted)

        self.assertEqual(parsed_quote["quote_svrid"], "2305102652627502401")
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["item_count"], 0)
        self.assertIn("卡兹克: [图片]", payload["text"])
        self.assertIn("Atom&原子君: 这是什么道理？", payload["text"])

    def test_image_cdn_fields_accept_attributes_and_child_nodes(self):
        info = self.handler._parse_image_cdn_info(
            '<dataitem><cdnthumburl>thumb</cdnthumburl><cdnthumbkey>key</cdnthumbkey>'
            '&lt;img cdnmidimgurl=&quot;mid&quot; cdnbigimgurl=&quot;big&quot;/&gt;</dataitem>'
        )
        self.assertEqual(info["aes_key"], "key")
        self.assertEqual(info["file_id_thumb"], "thumb")
        self.assertEqual(info["file_id_mid"], "mid")
        self.assertEqual(info["file_id_big"], "big")

    def test_wechat4_original_cdn_pair_is_preferred_over_thumbnail(self):
        info = self.handler._parse_image_cdn_info(
            '<dataitem><cdndataurl>original-id</cdndataurl>'
            '<cdndatakey>original-key</cdndatakey><filetype>1</filetype>'
            '<cdnthumburl>thumb-id</cdnthumburl><cdnthumbkey>thumb-key</cdnthumbkey>'
            '<thumbfiletype>1</thumbfiletype>'
            '</dataitem>'
        )

        self.assertEqual(info["aes_key"], "original-key")
        self.assertEqual(
            self.handler._image_download_candidates(info),
            [
                ("original-id", "original-key", 1),
                ("thumb-id", "thumb-key", 1),
            ],
        )

    def test_svrid_cache_replaces_compact_quote_with_full_record(self):
        full_record = (
            '<recordinfo><desc>摘要</desc><datalist count="2">'
            '<dataitem datatype="1"><sourcename>甲</sourcename>'
            '<srcmsgcontent>完整文字</srcmsgcontent></dataitem>'
            '<dataitem datatype="2"><sourcename>甲</sourcename>'
            '<srcmsgcontent>&lt;msg&gt;&lt;img aeskey=&quot;k&quot; '
            'cdnmidimgurl=&quot;m&quot;/&gt;&lt;/msg&gt;</srcmsgcontent></dataitem>'
            '</datalist></recordinfo>'
        )
        compact_record = (
            '<recordinfo><desc>甲: 完整文字\n甲: [图片]</desc>'
            '<datalist count="0"/></recordinfo>'
        )
        self.handler._forward_record_cache_lock = threading.Lock()
        self.handler._forward_record_cache = {
            "42": {"raw_msg": build_forward_card(full_record)}
        }

        payload, cache_hit = self.handler._resolve_forward_record(
            "42", build_forward_card(compact_record)
        )

        self.assertTrue(cache_hit)
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["images"][0]["file_id_mid"], "m")
        self.assertIn("甲: 完整文字", payload["text"])


if __name__ == "__main__":
    unittest.main()
