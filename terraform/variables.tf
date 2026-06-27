variable "aws_region" {
  description = "The AWS Region to deploy the DR node in"
  type        = string
  default     = "ap-southeast-2" # Sydney
}

variable "instance_type" {
  description = "EC2 Instance Type"
  type        = string
  default     = "t3.large" # 2 vCPU, 8GB RAM (enough for Milvus + Postgres + API)
}

variable "ssh_key_name" {
  description = "Name of the SSH key pair configured in AWS"
  type        = string
  default     = "my-sydney-dr-key"
}
