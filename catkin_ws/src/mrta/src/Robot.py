import rospy
import utils
from mrta.msg import AuctionRequest, AuctionAck, Bid, Winner, ScheduledTasks
from mrta.srv import TerminateRobot, TerminateRobotResponse
from STN import STN
from copy import deepcopy
from Logger import Logger

class Robot():

    def __init__(self, _id, pos_x, pos_y, capability, speed):  
        self.logger = Logger()
        self.id = _id
        self.name = "robot" + str(self.id)
        self.init_pos = (pos_x, pos_y)
        self.speed = speed
        self.stn = STN(self.init_pos, self.speed)
        
        self._bid_alpha = 0.5  
        self._cost_alpha = 0.5
        self._capability = capability      
        self._completed_auctions = []
        self._t_auc = []
        self._best_task_pos = {}
        self._winner_received = True
        self._tasks_preconditions = {}
        self._auc_ack_pub = None
        self._bid_pub = None        
        self._scheduled_tasks_pub = None             
    
    def __str__(self):
        s = "id: " + str(self.id)
        s += ", init location: " + str(self.init_pos)
        return s

    def set_alpha(self, alpha):
        self._bid_alpha = alpha
        self._cost_alpha = alpha
    
    def start_listener(self):
        rospy.init_node(self.name, disable_signals=True)

        self._auc_ack_pub = rospy.Publisher("/auction_ack", AuctionAck, queue_size=10)
        self._bid_pub = rospy.Publisher("/bid", Bid, queue_size=10)
        self._scheduled_tasks_pub = rospy.Publisher("/scheduled_tasks", ScheduledTasks, queue_size=10)   
        rospy.Subscriber("/auction", AuctionRequest, self.auction_callback)
        rospy.Subscriber("/winner", Winner, self.winner_callback)
        rospy.Service(self.name + '/terminate', TerminateRobot, self.handle_robot_terminate)
        rospy.spin()

        return self.stn.get_makespan(), self.stn.total_travel_time

    def auction_callback(self, msg):    
        if msg.id in self._completed_auctions:
            return  

        auc_ack_msg = AuctionAck()
        auc_ack_msg.robot_id = int(self.id)
        auc_ack_msg.auc_id = int(msg.id)        
        self._auc_ack_pub.publish(auc_ack_msg)

        self.logger.debug("Robot {0}: Received auction {1} with following {2} tasks.".format(self.id, msg.id, len(msg.tasks)))        

        self._t_auc = utils.create_tasks(msg.tasks)

        for t in self._t_auc:
            self.logger.debug("{0}".format(str(t)))
        
        i = 0
        for task in self._t_auc:
            self._tasks_preconditions[task] = msg.PC[i]
            i += 1

        self._winner_received = True
        while len(self._t_auc) > 0:

            min_bid = float("inf")
            min_task = None

            if self._winner_received:
                self.logger.info("Robot {0}: Finding optimal task".format(self.id))
                for task in self._t_auc:
                    bid, best_pos = self._compute_min_bid(task)
                    self.logger.debug("Robot {0}: Bid for task {1} is {2}, and best pos is {3}".format(self.id, task.id, bid, best_pos))
                    self._best_task_pos[task.id] = best_pos 
                    if bid < min_bid:
                        min_bid = bid
                        min_task = task

                if min_task is not None:
                    self.logger.info("Robot {0}: Found optimal task".format(self.id))
                    bid_msg = Bid()
                    bid_msg.auc_id = msg.id
                    bid_msg.robot_id = self.id
                    bid_msg.task = utils.create_task_msg(min_task)
                    bid_msg.bid = min_bid

                    self.logger.info("Robot {0}: Publishing bid {1}".format(self.id, str(bid_msg.task)))
                    self._bid_pub.publish(bid_msg)
                    self._winner_received = False
                
        tasks = self._tighten_schedule()
        self.logger.info("Robot {0}: All tasks from the current auction has been scheduled.".format(self.id))
        self.logger.debug("Robot {0}: Makespan is {1}".format(self.id, self.stn.get_makespan()))
        self.logger.debug("\nRobot {0}: Schedule:\n {1}\n".format(self.id, str(self.stn)))
        
        self._publish_scheduled_tasks(tasks)        
        self._completed_auctions.append(msg.id)

    def winner_callback(self, msg):
        self.logger.info("Robot {0}: Winner received".format(self.id))
        
        winner_robot_id = msg.robot_id
        task = utils.create_task(msg.task)

        if winner_robot_id == self.id:            
            self.logger.info("Robot {0} is the winner".format(self.id))
            self.stn.insert_task(task, self._best_task_pos[task.id])
            self.stn.solve_stn(self._tasks_preconditions)    

        self.logger.debug("Robot {0}: Following task has been scheduled.".format(self.id))
        self.logger.debug("{0}".format(str(task)))

        self._t_auc.remove(task)
        self._winner_received = True             

    def handle_robot_terminate(self, req):
        rospy.signal_shutdown("Normal Shutdown")                
        return TerminateRobotResponse(True)
    
    def init_auction(self, auc_id, t_auct, PC):
        self._t_auc = deepcopy(t_auct)
        self.logger.debug("Robot {0}: Initializing Auction {1}".format(self.id, auc_id))

        for t in self._t_auc:
            self.logger.debug("{0}".format(str(t)))
        
        i = 0
        for task in self._t_auc:
            self._tasks_preconditions[task] = PC[i]
            i += 1

    def get_min_bid(self):
        min_bid = float("inf")
        min_task = None

        self.logger.info("Robot {0}: Finding optimal task".format(self.id))
        
        for task in self._t_auc:
            bid, best_pos = self._compute_min_bid(task)
            self.logger.debug("Robot {0}: Bid for task {1} is {2}, and best pos is {3}".format(self.id, task.id, bid, best_pos))
            self._best_task_pos[task.id] = best_pos 
            if bid < min_bid:
                min_bid = bid
                min_task = task

        return min_bid, min_task

    def notify_winner(self, auc_id, winner_robot_id, task):
        self.logger.info("Robot {0}: Winner received".format(self.id))

        if winner_robot_id == self.id:            
            self.logger.info("Robot {0} is the winner".format(self.id))
            self.stn.insert_task(task, self._best_task_pos[task.id])
            self.stn.solve_stn(self._tasks_preconditions)

        self.logger.debug("Robot {0}: Following task has been scheduled.".format(self.id))
        self.logger.debug("{0}".format(str(task)))
        self._t_auc.remove(task)

    def end_auction(self):
        self._t_auc = []
    
        self.logger.warn("Robot {0}: Ending auction".format(self.id))        

    def get_scheduled_tasks(self, auc_id):
        tasks = self._tighten_schedule()
        self.logger.info("Robot {0}: All tasks from the current auction has been scheduled.".format(self.id))
        self.logger.debug("Robot {0}: Makespan is {1}".format(self.id, self.stn.get_makespan()))
        self.logger.debug("\nRobot {0}: Schedule:\n {1}\n".format(self.id, str(self.stn)))
        self._completed_auctions.append(auc_id)
        return tasks

    def is_capable(self, task):
        #if task.type not in self._capability:
        #    return False

        return True

    def find_min_cost(self, task, pc):
        task_count = self.stn.task_count
        min_cost = float("inf")
        min_pos = None
        
        for i in range(task_count + 1):   
            temp_stn = deepcopy(self.stn)

            tt_before = temp_stn.total_travel_time
            temp_stn.insert_task(task, i)
            temp_stn.solve_stn(pc)              
            tt_after = temp_stn.total_travel_time

            if temp_stn.is_consistent():
                addition_travel_time = tt_after - tt_before
                cost = self._compute_cost(temp_stn.get_makespan(), addition_travel_time)
                if cost < min_cost:
                    min_cost = cost
                    min_pos = i        

        return min_cost, min_pos

    def add_task(self, task, pos, pc):
        self.stn.insert_task(task, pos)
        self.stn.solve_stn(pc)

    def _publish_scheduled_tasks(self, tasks):
        scheduled_tasks_msg = ScheduledTasks()
        scheduled_tasks_msg.robot_id = self.id
        scheduled_tasks_msg.tasks = tasks
        scheduled_tasks_msg.makespan = self.stn.get_makespan()
        scheduled_tasks_msg.travel_time = self.stn.total_travel_time
        
        self._scheduled_tasks_pub.publish(scheduled_tasks_msg)

    def _tighten_schedule(self):
        self.logger.info("Robot {0}: Tightening schedule".format(self.id))
        tasks = []
        for i in range(self.stn.task_count):
            task = self.stn._get_task(i)            
            f = task.finish_time

            task.update_time_window(task.est, f)
            self.stn.update_task_constraints(task.id)
            tasks.append(task)
        return tasks    

    def _compute_min_bid(self, task):
        task_count = self.stn.task_count
        min_bid = float("inf")
        min_pos = None

        for i in range(task_count + 1):  
            temp_stn = deepcopy(self.stn)

            tt_before = temp_stn.total_travel_time
            temp_stn.insert_task(task, i)
            temp_stn.solve_stn(self._tasks_preconditions)              
            tt_after = temp_stn.total_travel_time

            if temp_stn.is_consistent():
                addition_travel_time = tt_after - tt_before
                bid = self._compute_bid(temp_stn.get_makespan(), addition_travel_time)
                if bid < min_bid:
                    min_bid = bid
                    min_pos = i

        return min_bid, min_pos

    def _compute_bid(self, makespan, travel_time):
        return makespan * self._bid_alpha + travel_time * (1 - self._bid_alpha)

    def _compute_cost(self, makespan, travel_time):
        return makespan * self._cost_alpha + travel_time * (1 - self._cost_alpha)
