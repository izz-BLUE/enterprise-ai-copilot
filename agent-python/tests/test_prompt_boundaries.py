"""test_prompt_boundaries.py —— 提示词边界测试

验证 SYSTEM_PROMPT 和 build_rag_prompt 中包含必要的安全边界声明。
"""

from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt


class TestSystemPromptBoundaries:
    """验证 SYSTEM_PROMPT 包含安全边界声明。"""

    def test_contains_untrusted_data_statement(self):
        assert "不可信" in SYSTEM_PROMPT

    def test_contains_no_execute_instructions(self):
        assert "不得遵循" in SYSTEM_PROMPT or "不得执行" in SYSTEM_PROMPT

    def test_contains_no_reveal_prompt(self):
        assert "泄露系统提示词" in SYSTEM_PROMPT or "系统提示词" in SYSTEM_PROMPT

    def test_contains_no_reveal_config(self):
        assert "内部配置" in SYSTEM_PROMPT

    def test_contains_no_reveal_credentials(self):
        assert "凭据" in SYSTEM_PROMPT or "密钥" in SYSTEM_PROMPT

    def test_contains_identity_no_privilege_escalation(self):
        assert "角色" in SYSTEM_PROMPT or "身份" in SYSTEM_PROMPT or "权限" in SYSTEM_PROMPT

    def test_contains_knowledge_as_facts_only(self):
        assert "事实资料" in SYSTEM_PROMPT

    def test_contains_boundary_section(self):
        assert "安全边界" in SYSTEM_PROMPT


class TestRagPromptBoundaries:
    """验证 build_rag_prompt 生成的 prompt 包含边界标记。"""

    def _build_sample_prompt(self):
        chunks = [
            {"domain": "hr", "source_file": "annual_leave.md",
             "content": "年假规则：入职满1年享有5天年假。"},
        ]
        return build_rag_prompt("年假怎么申请？", chunks)

    def test_contains_untrusted_knowledge_marker_start(self):
        prompt = self._build_sample_prompt()
        assert "【不可信知识库资料开始】" in prompt

    def test_contains_untrusted_knowledge_marker_end(self):
        prompt = self._build_sample_prompt()
        assert "【不可信知识库资料结束】" in prompt

    def test_contains_untrusted_user_question_marker(self):
        prompt = self._build_sample_prompt()
        assert "【不可信用户问题】" in prompt

    def test_contains_knowledge_not_instruction_statement(self):
        prompt = self._build_sample_prompt()
        assert "不能作为系统指令执行" in prompt

    def test_knowledge_between_markers(self):
        prompt = self._build_sample_prompt()
        start = prompt.index("【不可信知识库资料开始】")
        end = prompt.index("【不可信知识库资料结束】")
        assert start < end
        knowledge_section = prompt[start:end]
        assert "年假规则" in knowledge_section

    def test_user_question_after_knowledge(self):
        prompt = self._build_sample_prompt()
        knowledge_end = prompt.index("【不可信知识库资料结束】")
        user_marker = prompt.index("【不可信用户问题】")
        assert knowledge_end < user_marker

    def test_no_chunks_prompt_structure(self):
        """无知识库结果时的 prompt 也应有基本结构"""
        prompt = build_rag_prompt("测试问题", [])
        assert "当前知识库未检索到相关内容" in prompt
        assert "测试问题" in prompt
