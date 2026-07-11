#include <cmath>
#include <functional>
#include <memory>
#include <string>

#include <geometry_msgs/msg/transform_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <tf2/LinearMath/Matrix3x3.h>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Transform.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>
#include <tf2_ros/static_transform_broadcaster.h>
#include <tf2_ros/transform_broadcaster.h>

class LioPlanarProjection : public rclcpp::Node
{
public:
  LioPlanarProjection()
  : Node("lio_planar_projection")
  {
    raw_odom_topic_ = declare_parameter<std::string>("raw_odom_topic", "/odom_lio_raw");
    projected_odom_topic_ = declare_parameter<std::string>("projected_odom_topic", "/odom_lio");
    odom_frame_ = declare_parameter<std::string>("odom_frame", "odom");
    lio_odom_frame_ = declare_parameter<std::string>("lio_odom_frame", "lio_odom");
    lio_base_frame_ = declare_parameter<std::string>("lio_base_frame", "lio_base");
    base_frame_ = declare_parameter<std::string>("base_frame", "base_footprint");

    const double world_yaw = declare_parameter<double>("odom_to_lio_odom_yaw", 0.0);
    projected_z_ = declare_parameter<double>("projected_z", 0.0);

    const double lio_to_base_x = declare_parameter<double>("lio_to_base_x", 0.0);
    const double lio_to_base_y = declare_parameter<double>("lio_to_base_y", 0.0);
    const double lio_to_base_z = declare_parameter<double>("lio_to_base_z", 0.0);
    const double lio_to_base_roll = declare_parameter<double>("lio_to_base_roll", 0.0);
    const double lio_to_base_pitch = declare_parameter<double>("lio_to_base_pitch", 0.0);
    const double lio_to_base_yaw = declare_parameter<double>("lio_to_base_yaw", 0.0);

    tf2::Quaternion world_rotation;
    world_rotation.setRPY(0.0, 0.0, world_yaw);
    odom_to_lio_odom_.setOrigin(tf2::Vector3(0.0, 0.0, 0.0));
    odom_to_lio_odom_.setRotation(world_rotation);

    tf2::Quaternion lio_to_base_rotation;
    lio_to_base_rotation.setRPY(lio_to_base_roll, lio_to_base_pitch, lio_to_base_yaw);
    lio_to_base_.setOrigin(tf2::Vector3(lio_to_base_x, lio_to_base_y, lio_to_base_z));
    lio_to_base_.setRotation(lio_to_base_rotation);

    tf_broadcaster_ = std::make_shared<tf2_ros::TransformBroadcaster>(*this);
    static_tf_broadcaster_ = std::make_shared<tf2_ros::StaticTransformBroadcaster>(*this);
    projected_odom_publisher_ = create_publisher<nav_msgs::msg::Odometry>(projected_odom_topic_, 10);
    raw_odom_subscription_ = create_subscription<nav_msgs::msg::Odometry>(
      raw_odom_topic_, 10,
      std::bind(&LioPlanarProjection::rawOdomCallback, this, std::placeholders::_1));

    publishWorldAlignment();
  }

private:
  void publishWorldAlignment()
  {
    geometry_msgs::msg::TransformStamped transform;
    transform.header.stamp = now();
    transform.header.frame_id = odom_frame_;
    transform.child_frame_id = lio_odom_frame_;
    transform.transform = tf2::toMsg(odom_to_lio_odom_);
    static_tf_broadcaster_->sendTransform(transform);
  }

  void rawOdomCallback(const nav_msgs::msg::Odometry::SharedPtr message)
  {
    if (message->header.frame_id != lio_odom_frame_ ||
      message->child_frame_id != lio_base_frame_)
    {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000,
        "Ignoring raw Point-LIO odom with frames '%s' -> '%s'; expected '%s' -> '%s'.",
        message->header.frame_id.c_str(), message->child_frame_id.c_str(),
        lio_odom_frame_.c_str(), lio_base_frame_.c_str());
      return;
    }

    const auto &position = message->pose.pose.position;
    const auto &orientation = message->pose.pose.orientation;
    const double norm = std::sqrt(
      orientation.x * orientation.x + orientation.y * orientation.y +
      orientation.z * orientation.z + orientation.w * orientation.w);
    if (!std::isfinite(position.x) || !std::isfinite(position.y) ||
      !std::isfinite(position.z) || !std::isfinite(norm) || norm < 1e-6)
    {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000,
        "Ignoring invalid raw Point-LIO odometry pose.");
      return;
    }

    tf2::Transform lio_odom_to_lio_base;
    tf2::fromMsg(message->pose.pose, lio_odom_to_lio_base);
    tf2::Quaternion raw_rotation = lio_odom_to_lio_base.getRotation();
    raw_rotation.normalize();
    lio_odom_to_lio_base.setRotation(raw_rotation);

    const tf2::Transform odom_to_lio_base = odom_to_lio_odom_ * lio_odom_to_lio_base;
    const tf2::Transform raw_odom_to_base = odom_to_lio_base * lio_to_base_;

    double roll = 0.0;
    double pitch = 0.0;
    double yaw = 0.0;
    tf2::Matrix3x3(raw_odom_to_base.getRotation()).getRPY(roll, pitch, yaw);

    tf2::Quaternion planar_rotation;
    planar_rotation.setRPY(0.0, 0.0, yaw);
    const tf2::Transform odom_to_base_planar(
      planar_rotation,
      tf2::Vector3(
        raw_odom_to_base.getOrigin().x(),
        raw_odom_to_base.getOrigin().y(),
        projected_z_));

    const tf2::Transform lio_base_to_base_planar =
      odom_to_lio_base.inverse() * odom_to_base_planar;

    geometry_msgs::msg::TransformStamped transform;
    transform.header.stamp = message->header.stamp;
    transform.header.frame_id = lio_base_frame_;
    transform.child_frame_id = base_frame_;
    transform.transform = tf2::toMsg(lio_base_to_base_planar);
    tf_broadcaster_->sendTransform(transform);

    nav_msgs::msg::Odometry projected_odom;
    projected_odom.header.stamp = message->header.stamp;
    projected_odom.header.frame_id = odom_frame_;
    projected_odom.child_frame_id = base_frame_;
    projected_odom.pose.pose = tf2::toMsg(odom_to_base_planar);
    projected_odom.pose.covariance = message->pose.covariance;
    projected_odom.twist.twist.linear.x = message->twist.twist.linear.x;
    projected_odom.twist.twist.linear.y = message->twist.twist.linear.y;
    projected_odom.twist.twist.angular.z = message->twist.twist.angular.z;
    projected_odom.twist.covariance = message->twist.covariance;
    projected_odom_publisher_->publish(projected_odom);
  }

  std::string raw_odom_topic_;
  std::string projected_odom_topic_;
  std::string odom_frame_;
  std::string lio_odom_frame_;
  std::string lio_base_frame_;
  std::string base_frame_;
  double projected_z_{};
  tf2::Transform odom_to_lio_odom_;
  tf2::Transform lio_to_base_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr raw_odom_subscription_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr projected_odom_publisher_;
  std::shared_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;
  std::shared_ptr<tf2_ros::StaticTransformBroadcaster> static_tf_broadcaster_;
};

int main(int argc, char **argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<LioPlanarProjection>());
  rclcpp::shutdown();
  return 0;
}
