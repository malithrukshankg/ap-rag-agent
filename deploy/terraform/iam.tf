# Instance role, least privilege: pull this one image, read these two parameters, and register
# with SSM for Session Manager / Run Command. Nothing else.

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.project}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action does not support resource-level permissions
  }

  statement {
    sid = "EcrPullThisRepo"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid     = "ReadAppSecrets"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.anthropic_key_param}",
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.hmac_key_param}",
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.langfuse_pub_param}",
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.langfuse_sec_param}",
    ]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${var.project}-app"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project}-instance"
  role = aws_iam_role.app.name
}
