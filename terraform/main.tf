terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# 1. Configure the AWS Provider (We target the Sydney region as our DR node)
provider "aws" {
  region = var.aws_region
}

# 2. Create a Security Group to allow SSH (22) and Web/API traffic (80, 8000, 8001)
resource "aws_security_group" "dr_sg" {
  name        = "enterprise-rag-dr-sg"
  description = "Security group for AI Analyst Disaster Recovery node"

  ingress {
    description = "SSH Access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # In production, restrict this to your corporate VPN IP
  }

  ingress {
    description = "Gateway API"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 3. Provision the EC2 Instance (This is the actual "Server")
resource "aws_instance" "dr_standby_node" {
  ami           = "ami-0c1a8bf9f1165a257" # Ubuntu 22.04 LTS in ap-southeast-2 (Sydney)
  instance_type = var.instance_type       # e.g., t3.large (8GB RAM)

  vpc_security_group_ids = [aws_security_group.dr_sg.id]
  key_name               = var.ssh_key_name # Your AWS Key Pair name

  # Configure a 50GB SSD for our Docker containers and Vector DB
  root_block_device {
    volume_size = 50
    volume_type = "gp3"
  }

  tags = {
    Name        = "DR-Standby-Sydney-AI-Engine"
    Environment = "DisasterRecovery"
    ManagedBy   = "Terraform"
  }
}
