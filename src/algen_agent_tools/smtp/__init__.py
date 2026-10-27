"""Send email over SMTP from the deployment's own address.

Replaces the Runtime's ``communications.email_tool`` and ``SmtpEmailSender``.
"""

from algen_agent_tools.smtp.sender import SmtpEmailSender, SmtpSettings
from algen_agent_tools.smtp.toolkit import smtp_toolkit

__all__ = ["SmtpEmailSender", "SmtpSettings", "smtp_toolkit"]
