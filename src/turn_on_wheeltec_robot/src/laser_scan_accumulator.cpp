#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <functional>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "geometry_msgs/msg/transform_stamped.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/laser_scan.hpp"
#include "tf2/LinearMath/Transform.h"
#include "tf2/time.h"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/buffer.h"
#include "tf2_ros/transform_listener.h"

namespace
{

constexpr double kTwoPi = 6.28318530717958647692;

struct Point3
{
  double x;
  double y;
  double z;
};

struct TimedPoints
{
  rclcpp::Time stamp;
  std::vector<Point3> points;
};

struct VoxelKey
{
  std::int64_t x;
  std::int64_t y;

  bool operator==(const VoxelKey & other) const
  {
    return x == other.x && y == other.y;
  }
};

struct VoxelKeyHash
{
  std::size_t operator()(const VoxelKey & key) const
  {
    const auto hx = std::hash<std::int64_t>{}(key.x);
    const auto hy = std::hash<std::int64_t>{}(key.y);
    return hx ^ (hy + 0x9e3779b97f4a7c15ULL + (hx << 6U) + (hx >> 2U));
  }
};

}  // namespace

class LaserScanAccumulator : public rclcpp::Node
{
public:
  LaserScanAccumulator()
  : Node("laser_scan_accumulator"),
    tf_buffer_(this->get_clock()),
    tf_listener_(tf_buffer_)
  {
    input_topic_ = declare_parameter<std::string>("input_scan_topic", "/scan");
    output_topic_ =
      declare_parameter<std::string>("output_scan_topic", "/scan_accumulated");
    fixed_frame_ = declare_parameter<std::string>("fixed_frame", "odom");
    output_frame_ = declare_parameter<std::string>("output_frame", "base_link");
    window_duration_ = declare_parameter<double>("window_duration", 3.0);
    voxel_size_ = declare_parameter<double>("voxel_size", 0.04);
    minimum_coverage_ = declare_parameter<double>("minimum_coverage", 0.35);
    transform_timeout_ = declare_parameter<double>("transform_timeout", 0.15);

    if (window_duration_ <= 0.0) {
      throw std::invalid_argument("window_duration must be positive");
    }
    if (voxel_size_ <= 0.0) {
      throw std::invalid_argument("voxel_size must be positive");
    }
    if (minimum_coverage_ < 0.0 || minimum_coverage_ > 1.0) {
      throw std::invalid_argument("minimum_coverage must be between 0 and 1");
    }

    scan_pub_ = create_publisher<sensor_msgs::msg::LaserScan>(
      output_topic_, rclcpp::SensorDataQoS());
    scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
      input_topic_, rclcpp::SensorDataQoS(),
      std::bind(&LaserScanAccumulator::scan_callback, this, std::placeholders::_1));

    RCLCPP_INFO(
      get_logger(),
      "Motion-compensated scan accumulation: %s -> %s, fixed=%s, output=%s, "
      "window=%.2fs, voxel=%.3fm, minimum coverage=%.0f%%",
      input_topic_.c_str(), output_topic_.c_str(), fixed_frame_.c_str(),
      output_frame_.c_str(), window_duration_, voxel_size_, minimum_coverage_ * 100.0);
  }

private:
  void scan_callback(const sensor_msgs::msg::LaserScan::SharedPtr scan)
  {
    if (scan->ranges.empty() || scan->angle_increment <= 0.0) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "Ignoring an empty or invalid LaserScan");
      return;
    }

    const rclcpp::Time stamp(scan->header.stamp);
    if (!frames_.empty() && stamp < frames_.back().stamp) {
      RCLCPP_WARN(get_logger(), "ROS time moved backwards; clearing accumulated scans");
      frames_.clear();
      ready_ = false;
    }

    geometry_msgs::msg::TransformStamped fixed_from_scan_msg;
    geometry_msgs::msg::TransformStamped output_from_fixed_msg;
    try {
      fixed_from_scan_msg = tf_buffer_.lookupTransform(
        fixed_frame_, scan->header.frame_id, stamp,
        tf2::durationFromSec(transform_timeout_));
      output_from_fixed_msg = tf_buffer_.lookupTransform(
        output_frame_, fixed_frame_, stamp,
        tf2::durationFromSec(transform_timeout_));
    } catch (const tf2::TransformException & error) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 3000,
        "Waiting for timestamped scan transforms (%s -> %s -> %s): %s",
        scan->header.frame_id.c_str(), fixed_frame_.c_str(), output_frame_.c_str(),
        error.what());
      return;
    }

    tf2::Transform fixed_from_scan;
    tf2::Transform output_from_fixed;
    tf2::fromMsg(fixed_from_scan_msg.transform, fixed_from_scan);
    tf2::fromMsg(output_from_fixed_msg.transform, output_from_fixed);

    TimedPoints frame;
    frame.stamp = stamp;
    frame.points.reserve(scan->ranges.size());
    for (std::size_t index = 0; index < scan->ranges.size(); ++index) {
      const double range = scan->ranges[index];
      if (!std::isfinite(range) || range < scan->range_min || range > scan->range_max) {
        continue;
      }

      const double angle = scan->angle_min + static_cast<double>(index) * scan->angle_increment;
      const tf2::Vector3 point_in_scan(range * std::cos(angle), range * std::sin(angle), 0.0);
      const tf2::Vector3 point_in_fixed = fixed_from_scan * point_in_scan;
      frame.points.push_back(
        {point_in_fixed.x(), point_in_fixed.y(), point_in_fixed.z()});
    }
    frames_.push_back(std::move(frame));

    const rclcpp::Duration window = rclcpp::Duration::from_seconds(window_duration_);
    while (!frames_.empty() && stamp - frames_.front().stamp > window) {
      frames_.pop_front();
    }

    std::unordered_map<VoxelKey, Point3, VoxelKeyHash> points_by_voxel;
    for (const auto & stored_frame : frames_) {
      for (const auto & point : stored_frame.points) {
        const VoxelKey key{
          static_cast<std::int64_t>(std::floor(point.x / voxel_size_)),
          static_cast<std::int64_t>(std::floor(point.y / voxel_size_))};
        points_by_voxel[key] = point;
      }
    }

    auto accumulated = *scan;
    accumulated.header.frame_id = output_frame_;
    accumulated.time_increment = 0.0;
    accumulated.ranges.assign(
      scan->ranges.size(), std::numeric_limits<float>::infinity());
    accumulated.intensities.clear();

    const double angular_span = scan->angle_max - scan->angle_min;
    const bool full_circle = angular_span >= (kTwoPi - 2.0 * scan->angle_increment);
    for (const auto & item : points_by_voxel) {
      const auto & point = item.second;
      const tf2::Vector3 point_in_output =
        output_from_fixed * tf2::Vector3(point.x, point.y, point.z);
      const double range = std::hypot(point_in_output.x(), point_in_output.y());
      if (range < accumulated.range_min || range > accumulated.range_max) {
        continue;
      }

      double angle = std::atan2(point_in_output.y(), point_in_output.x());
      if (full_circle) {
        while (angle < accumulated.angle_min) {
          angle += kTwoPi;
        }
        while (angle > accumulated.angle_max) {
          angle -= kTwoPi;
        }
      }
      if (angle < accumulated.angle_min || angle > accumulated.angle_max) {
        continue;
      }

      const auto bin = static_cast<std::int64_t>(
        std::llround((angle - accumulated.angle_min) / accumulated.angle_increment));
      if (bin < 0 || static_cast<std::size_t>(bin) >= accumulated.ranges.size()) {
        continue;
      }
      auto & current_range = accumulated.ranges[static_cast<std::size_t>(bin)];
      current_range = std::min(current_range, static_cast<float>(range));
    }

    const auto valid_bins = static_cast<std::size_t>(std::count_if(
      accumulated.ranges.begin(), accumulated.ranges.end(),
      [](float range) {return std::isfinite(range);}));
    const double coverage =
      static_cast<double>(valid_bins) / static_cast<double>(accumulated.ranges.size());
    if (!ready_ && coverage < minimum_coverage_) {
      RCLCPP_INFO_THROTTLE(
        get_logger(), *get_clock(), 3000,
        "Accumulating scans: %zu frames, %zu/%zu valid bins (%.1f%%), need %.1f%%",
        frames_.size(), valid_bins, accumulated.ranges.size(), coverage * 100.0,
        minimum_coverage_ * 100.0);
      return;
    }

    if (!ready_) {
      ready_ = true;
      RCLCPP_INFO(
        get_logger(),
        "Scan accumulator warmed up with %zu frames and %.1f%% valid-bin coverage",
        frames_.size(), coverage * 100.0);
    }

    scan_pub_->publish(accumulated);
    RCLCPP_INFO_THROTTLE(
      get_logger(), *get_clock(), 5000,
      "Publishing accumulated scan: %zu frames, %zu/%zu valid bins (%.1f%%)",
      frames_.size(), valid_bins, accumulated.ranges.size(), coverage * 100.0);
  }

  std::string input_topic_;
  std::string output_topic_;
  std::string fixed_frame_;
  std::string output_frame_;
  double window_duration_;
  double voxel_size_;
  double minimum_coverage_;
  double transform_timeout_;

  tf2_ros::Buffer tf_buffer_;
  tf2_ros::TransformListener tf_listener_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub_;
  std::deque<TimedPoints> frames_;
  bool ready_{false};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<LaserScanAccumulator>());
  rclcpp::shutdown();
  return 0;
}
