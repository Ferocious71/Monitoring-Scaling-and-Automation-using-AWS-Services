import boto3
import base64
import time

# Initialize boto3 clients
s3_client = boto3.client('s3')
ec2_client = boto3.client('ec2')
elb_client = boto3.client('elbv2')
asg_client = boto3.client('autoscaling')
sns_client = boto3.client('sns')

# 1. Create S3 Bucket
bucket_name = 'my-webapp-static-bucket'
s3_client.create_bucket(Bucket=bucket_name)
print(f'S3 Bucket launched: {bucket_name}')

# 2. Launch EC2 Instance
response = ec2_client.run_instances(
    ImageId='ami-01816d07b1128cd2d',  # Example Amazon Linux AMI ID
    InstanceType='t2.micro',
    KeyName='sal_instance_new',
    MinCount=1,
    MaxCount=1,
    SecurityGroupIds=['sg-01f41ec5b97d3998c'],  # Replace with your SG ID
    SubnetId='subnet-01874c4512136bd62',  # Replace with your Subnet ID
    UserData="""#!/bin/bash
    yum update -y
    yum install -y httpd
    systemctl start httpd
    systemctl enable httpd
    echo "<html><h1>Welcome to the Web App</h1></html>" > /var/www/html/index.html
    """
)

instance_id = response['Instances'][0]['InstanceId']
print(f'EC2 Instance launched: {instance_id}')

# 3. Wait until the instance is in a running state
print(f"Waiting for EC2 instance {instance_id} to enter running state...")
ec2_client.get_waiter('instance_running').wait(InstanceIds=[instance_id])
print(f"EC2 Instance {instance_id} is now running.")

# 4. Add a Name tag to the EC2 instance
ec2_client.create_tags(
    Resources=[instance_id],
    Tags=[{'Key': 'Name', 'Value': 'Sal-MyWebAppInstance'}]  # Name the instance
)
print(f'Instance name: Sal-MyWebAppInstance')

# 5. Create Load Balancer and Target Group only after EC2 instance is running

# Create Load Balancer
lb_response = elb_client.create_load_balancer(
    Name='sal-my-alb',
    Subnets=['subnet-01874c4512136bd62', 'subnet-08fa616f96d54dfc2'],  # Replace with your Subnet IDs
    SecurityGroups=['sg-01f41ec5b97d3998c'],  # Replace with your SG ID
    Scheme='internet-facing',
    Type='application',
)

lb_arn = lb_response['LoadBalancers'][0]['LoadBalancerArn']
print(f"Load Balancer created: {lb_arn}")

# Create Target Group
tg_response = elb_client.create_target_group(
    Name='sal-target-group',
    Protocol='HTTP',
    Port=80,
    VpcId='vpc-09f02049d6176fe30',  # Replace with your VPC ID
    TargetType='instance',
)

tg_arn = tg_response['TargetGroups'][0]['TargetGroupArn']
print(f"Target Group created: {tg_arn}")

# Register EC2 instance with Target Group
elb_client.register_targets(
    TargetGroupArn=tg_arn,
    Targets=[{'Id': instance_id}],
)
print(f"EC2 instance {instance_id} registered with Target Group {tg_arn}")

# Wait for the instance to become healthy in the Target Group
'''print(f"Waiting for EC2 instance {instance_id} to become healthy in the Target Group...")
elb_client.get_waiter('target_in_service').wait(TargetGroupArn=tg_arn, Targets=[{'Id': instance_id}])
print(f"EC2 instance {instance_id} is now healthy in the Target Group.")'''

# Create Listener for the Load Balancer
elb_client.create_listener(
    LoadBalancerArn=lb_arn,
    Protocol='HTTP',
    Port=80,
    DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_arn}],
)

# 6. Create Auto Scaling Group and Launch Template

# Generate a unique name for the launch template
unique_name = f"sal-my-launch-template-{int(time.time())}"

# UserData script
user_data_script = """#!/bin/bash
yum update -y
yum install -y httpd
systemctl start httpd
systemctl enable httpd
echo "<html><h1>Welcome to the Web App</h1></html>" > /var/www/html/index.html
"""

# Encode the UserData in Base64
encoded_user_data = base64.b64encode(user_data_script.encode("utf-8")).decode("utf-8")

# Create Launch Template
lt_response = ec2_client.create_launch_template(
    LaunchTemplateName=unique_name,
    LaunchTemplateData={
        'ImageId': 'ami-01816d07b1128cd2d',  # Example AMI
        'InstanceType': 't2.micro',
        'KeyName': 'sal_instance_new',
        'SecurityGroupIds': ['sg-01f41ec5b97d3998c'],  # Security Group for instance
        'UserData': encoded_user_data,  # Use Base64-encoded UserData
    }
)

# Extract Launch Template ID
lt_id = lt_response['LaunchTemplate']['LaunchTemplateId']
print(f"Launch Template created with ID: {lt_id}")

# Create Auto Scaling Group
asg_name = 'sal-my-asg'
asg_client.create_auto_scaling_group(
    AutoScalingGroupName=asg_name,
    LaunchTemplate={'LaunchTemplateId': lt_id},
    MinSize=1,
    MaxSize=2,
    DesiredCapacity=1,
    VPCZoneIdentifier='subnet-01874c4512136bd62,subnet-08fa616f96d54dfc2',  # Subnets for instance placement
)

# Retrieve and Print Auto Scaling Group Details
asg_details = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
print("Auto Scaling Group Details:")
for group in asg_details['AutoScalingGroups']:
    print(f"Name: {group['AutoScalingGroupName']}")
    print(f"Launch Template: {group['LaunchTemplate']['LaunchTemplateId']}")
    print(f"Instances: {group['Instances']}")
    print(f"Min Size: {group['MinSize']}, Max Size: {group['MaxSize']}, Desired Capacity: {group['DesiredCapacity']}")
    print(f"Subnets: {group['VPCZoneIdentifier']}")

# Attach Scaling Policy
policy_name = 'scale-out'
asg_client.put_scaling_policy(
    AutoScalingGroupName=asg_name,
    PolicyName=policy_name,
    PolicyType='TargetTrackingScaling',
    TargetTrackingConfiguration={
        'PredefinedMetricSpecification': {'PredefinedMetricType': 'ASGAverageCPUUtilization'},
        'TargetValue': 5.0,
    }
)

# Retrieve and Print Scaling Policy Details
policy_details = asg_client.describe_policies(AutoScalingGroupName=asg_name, PolicyNames=[policy_name])
print("Scaling Policy Details:")
for policy in policy_details['ScalingPolicies']:
    print(f"Policy Name: {policy['PolicyName']}")
    print(f"Policy Type: {policy['PolicyType']}")
    print(f"Target Tracking Configuration: {policy['TargetTrackingConfiguration']}")

# 7. SNS - Subscription

# Create SNS Topic
sns_topic = sns_client.create_topic(Name='webapp-alerts')
sns_topic_arn = sns_topic['TopicArn']

# Subscribe Email
sns_client.subscribe(
    TopicArn=sns_topic_arn,
    Protocol='email',
    Endpoint='salmanmuneeb@gmail.com',  # Replace with your email
)
print('Subscription created. Check your email to confirm.')

